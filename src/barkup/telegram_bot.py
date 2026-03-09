"""Telegram bot for bark notifications and intervention tracking."""

import calendar
import logging
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from barkup.config import settings
from barkup.models import Episode

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}"


class TelegramBot:
    def __init__(self, on_intervention: callable = None, notion_logger=None,
                 on_file_request: callable = None):
        """
        Args:
            on_intervention: Callback(page_id, fields) for intervention updates.
            notion_logger: NotionLogger instance for looking up pages by message ID.
            on_file_request: Callback(page_id, file_type) -> file_path or None.
                           file_type is "clip", "video", or "snapshot".
        """
        self._token = settings.telegram_bot_token
        self._chat_id = settings.telegram_chat_id
        self._base_url = TELEGRAM_API.format(token=self._token)
        self._client = httpx.Client(timeout=30)
        self._on_intervention = on_intervention
        self._on_file_request = on_file_request
        self._notion = notion_logger
        # Allowed user IDs (owner + anyone they add)
        allowed = settings.telegram_allowed_users
        self._allowed_users: set[str] = set()
        if allowed:
            self._allowed_users = {uid.strip() for uid in allowed.split(",")}
        if self._chat_id:
            self._allowed_users.add(str(self._chat_id))
        self._poll_thread = None
        self._running = False
        self._last_update_id = 0

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._chat_id)

    def _send(self, method: str, **params) -> dict | None:
        try:
            resp = self._client.post(
                f"{self._base_url}/{method}", json=params
            )
            resp.raise_for_status()
            return resp.json().get("result")
        except Exception:
            logger.exception("Telegram API error: %s", method)
            return None

    def _is_authorized(self, user_id: int) -> bool:
        return str(user_id) in self._allowed_users

    def send_preliminary_notification(self, timestamp: datetime, camera_name: str | None = None,
                                       nest_link: str | None = None) -> int | None:
        """Send a preliminary sound detection notification. Returns the message ID."""
        tz = ZoneInfo(settings.timezone)
        local_start = timestamp.astimezone(tz) if timestamp.tzinfo else timestamp.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
        local_time = local_start.strftime("%I:%M:%S %p")

        cam_line = f"📷 Camera: {camera_name}\n" if camera_name else ""
        text = (
            f"🔊 *Sound Detected*\n\n"
            f"{cam_line}"
            f"⏰ Time: {local_time}\n"
            f"🔍 Analyzing audio...\n"
        )

        if nest_link:
            text += f"\n[View in Nest]({nest_link})"

        result = self._send(
            "sendMessage",
            chat_id=self._chat_id,
            text=text,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )

        if result:
            return result.get("message_id")
        return None

    def update_bark_notification(self, message_id: int, episode: Episode) -> None:
        """Update a preliminary notification with confirmed bark details."""
        duration = episode.duration_seconds
        if duration >= 60:
            dur_str = f"{duration / 60:.0f}m {duration % 60:.0f}s"
        else:
            dur_str = f"{duration:.0f}s"

        bark_secs = round(episode.bark_frame_count * 0.975, 1)
        if bark_secs >= 60:
            bark_str = f"{bark_secs / 60:.0f}m {bark_secs % 60:.0f}s"
        else:
            bark_str = f"{bark_secs:.0f}s"

        tz = ZoneInfo(settings.timezone)
        local_start = episode.start_time.astimezone(tz) if episode.start_time.tzinfo else episode.start_time.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
        local_time = local_start.strftime("%I:%M:%S %p")
        confidence_pct = episode.peak_confidence * 100

        cam_line = f"📷 Camera: {episode.camera_name}\n" if episode.camera_name else ""
        text = (
            f"🐕 *Bark Confirmed*\n\n"
            f"{cam_line}"
            f"⏰ Time: {local_time}\n"
            f"⏱ Duration: {dur_str} ({bark_str} barking, {episode.bark_frame_count} barks)\n"
            f"📊 Confidence: {confidence_pct:.0f}%\n"
            f"🔊 Type: {episode.dominant_bark_type.value}\n"
        )

        if episode.nest_link:
            text += f"\n[View in Nest]({episode.nest_link})"

        text += (
            f"\n\n_Reply to update:_\n"
            f"e.g. `home, intervened, it was the mailman`"
        )

        self._send(
            "editMessageText",
            chat_id=self._chat_id,
            message_id=message_id,
            text=text,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )

    def update_unconfirmed_notification(self, message_id: int, camera_name: str | None = None) -> None:
        """Update a preliminary notification to show no bark was confirmed."""
        cam_line = f"📷 Camera: {camera_name}\n" if camera_name else ""
        text = (
            f"✅ *No Bark Confirmed*\n\n"
            f"{cam_line}"
            f"Sound event analysed — not a bark.\n"
        )

        self._send(
            "editMessageText",
            chat_id=self._chat_id,
            message_id=message_id,
            text=text,
            parse_mode="Markdown",
        )

    def send_file(self, file_path: str, file_type: str, reply_to_message_id: int | None = None) -> None:
        """Send a file (audio, video, or photo) to the chat."""
        import os
        if not os.path.exists(file_path):
            self._send(
                "sendMessage",
                chat_id=self._chat_id,
                text=f"❌ File not found: {os.path.basename(file_path)}",
                reply_to_message_id=reply_to_message_id,
            )
            return

        method_map = {
            "clip": ("sendDocument", "document"),
            "video": ("sendVideo", "video"),
            "snapshot": ("sendPhoto", "photo"),
        }
        method, field = method_map.get(file_type, ("sendDocument", "document"))

        try:
            with open(file_path, "rb") as f:
                resp = self._client.post(
                    f"{self._base_url}/{method}",
                    data={"chat_id": self._chat_id, "reply_to_message_id": reply_to_message_id},
                    files={field: (os.path.basename(file_path), f)},
                    timeout=120,
                )
                resp.raise_for_status()
                logger.info("Sent %s file: %s", file_type, file_path)
        except Exception:
            logger.exception("Failed to send %s file", file_type)
            self._send(
                "sendMessage",
                chat_id=self._chat_id,
                text=f"❌ Failed to send {file_type}",
                reply_to_message_id=reply_to_message_id,
            )

    def send_bark_notification(self, episode: Episode, notion_page_id: str = None) -> int | None:
        """Send a bark episode notification. Returns the message ID."""
        duration = episode.duration_seconds
        if duration >= 60:
            dur_str = f"{duration / 60:.0f}m {duration % 60:.0f}s"
        else:
            dur_str = f"{duration:.0f}s"

        bark_secs = round(episode.bark_frame_count * 0.975, 1)
        if bark_secs >= 60:
            bark_str = f"{bark_secs / 60:.0f}m {bark_secs % 60:.0f}s"
        else:
            bark_str = f"{bark_secs:.0f}s"

        tz = ZoneInfo(settings.timezone)
        local_start = episode.start_time.astimezone(tz) if episode.start_time.tzinfo else episode.start_time.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
        local_time = local_start.strftime("%I:%M:%S %p")
        confidence_pct = episode.peak_confidence * 100

        cam_line = f"📷 Camera: {episode.camera_name}\n" if episode.camera_name else ""
        source_line = f"🔍 Source: {episode.source.value}\n" if hasattr(episode, 'source') else ""
        text = (
            f"🐕 *Bark Detected*\n\n"
            f"{cam_line}"
            f"⏰ Time: {local_time}\n"
            f"⏱ Duration: {dur_str} ({bark_str} barking, {episode.bark_frame_count} barks)\n"
            f"📊 Confidence: {confidence_pct:.0f}%\n"
            f"🔊 Type: {episode.dominant_bark_type.value}\n"
            f"{source_line}"
        )

        if episode.nest_link:
            text += f"\n[View in Nest]({episode.nest_link})"

        text += (
            f"\n\n_Reply to update:_\n"
            f"e.g. `home, intervened, it was the mailman`"
        )

        result = self._send(
            "sendMessage",
            chat_id=self._chat_id,
            text=text,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )

        if result:
            return result.get("message_id")
        return None

    def send_nightly_summary(self, episodes: list[dict], date: datetime = None):
        """Send the nightly summary at 8pm. Episodes are dicts from Notion query."""
        tz = ZoneInfo(settings.timezone)
        date = date or datetime.now(tz)
        date_str = date.strftime("%A, %B %d")

        if not episodes:
            text = (
                f"📋 *Daily Bark Summary - {date_str}*\n\n"
                f"✅ No barking episodes detected today! Good boy! 🐕"
            )
        else:
            total_bark_time = sum(e["bark_time_seconds"] for e in episodes)
            total_barks = sum(e["bark_count"] for e in episodes)
            if total_bark_time >= 60:
                total_str = f"{total_bark_time / 60:.0f}m {total_bark_time % 60:.0f}s"
            else:
                total_str = f"{total_bark_time:.0f}s"

            longest = max(episodes, key=lambda e: e["duration_seconds"])
            longest_dur = longest["duration_seconds"]
            if longest_dur >= 60:
                longest_str = f"{longest_dur / 60:.0f}m {longest_dur % 60:.0f}s"
            else:
                longest_str = f"{longest_dur:.0f}s"

            longest_time = longest["start_time"]
            longest_time_str = longest_time.astimezone(tz).strftime("%I:%M %p") if longest_time.tzinfo else longest_time.strftime("%I:%M %p")

            text = (
                f"📋 *Daily Bark Summary - {date_str}*\n\n"
                f"📊 Total episodes: {len(episodes)}\n"
                f"🐕 Total barks: {total_barks}\n"
                f"⏱ Total bark time: {total_str}\n"
                f"🔝 Longest episode: {longest_str} at {longest_time_str}\n\n"
            )

            # List each episode
            for i, ep in enumerate(episodes, 1):
                ep_time = ep["start_time"].astimezone(tz).strftime("%I:%M %p") if ep["start_time"].tzinfo else ep["start_time"].strftime("%I:%M %p")
                ep_dur = f"{ep['duration_seconds']:.0f}s"
                if ep["duration_seconds"] >= 60:
                    ep_dur = f"{ep['duration_seconds'] / 60:.0f}m"
                bark_info = f"{ep['bark_count']} barks" if ep["bark_count"] else ep_dur
                cam = f" [{ep['camera']}]" if ep.get("camera") else ""
                text += f"{i}. {ep_time} - {ep['bark_type']}{cam} ({bark_info})\n"

        self._send(
            "sendMessage",
            chat_id=self._chat_id,
            text=text,
            parse_mode="Markdown",
        )

    def _parse_reply(self, text: str) -> dict:
        """Parse user reply into intervention fields.

        Very flexible — just looks for keywords anywhere in the message.
        Examples that all work:
            "not bark" / "false positive" → marks as Not Bark
            "home"
            "I was home and intervened"
            "yes I was home, it was the mailman"
            "home, intervened, reason: stranger at door"
            "intervened - doorbell"
            "boredom"
            Any other text → added as a Notion comment
        """
        text_lower = text.lower().strip()
        result = {}

        # Check for file requests
        clip_phrases = ["clip", "audio", "send clip", "send audio", "sound"]
        video_phrases = ["video", "send video", "footage"]
        snapshot_phrases = ["snapshot", "photo", "image", "picture", "send photo", "send snapshot"]
        if any(text_lower == phrase or text_lower == f"send {phrase}" for phrase in ["clip", "audio", "sound"]):
            result["file_request"] = "clip"
            return result
        if any(text_lower == phrase or text_lower == f"send {phrase}" for phrase in ["video", "footage"]):
            result["file_request"] = "video"
            return result
        if any(text_lower == phrase or text_lower == f"send {phrase}" for phrase in ["snapshot", "photo", "image", "picture"]):
            result["file_request"] = "snapshot"
            return result

        # Check for "not bark" / "false positive" / "not a bark"
        not_bark_phrases = ["not bark", "not a bark", "false positive", "false alarm",
                           "wasn't bark", "wasnt bark", "no bark", "not barking"]
        if any(phrase in text_lower for phrase in not_bark_phrases):
            result["not_bark"] = True
            return result

        # Check for "was bark" / "confirmed" / "genuine" / "real" — validates events
        was_bark_phrases = ["was bark", "was a bark", "confirmed", "actually bark",
                           "real bark", "yes bark", "was barking", "genuine", "real"]
        if any(phrase in text_lower for phrase in was_bark_phrases):
            result["was_bark"] = True
            # Don't return early — continue parsing for home/away/reason

        # Check for "away" / "out" / "not home" → explicitly not home
        away_phrases = ["away", "not home", "wasn't home", "wasnt home", "weren't home",
                        "werent home", "nobody home", "no one home", "not at home"]
        if any(phrase in text_lower for phrase in away_phrases):
            result["was_home"] = False

        # Check for "home" / "was home" (only if not already set to False above)
        elif "home" in text_lower:
            result["was_home"] = True

        # Check for intervention
        intervene_words = ["intervene", "stopped", "told him", "told her",
                          "quieted", "calmed", "shushed", "went out"]
        if any(word in text_lower for word in intervene_words):
            result["intervened"] = True

        # Check for explicit "reason:" prefix
        for prefix in ["reason:", "reason :", "because ", "it was "]:
            if prefix in text_lower:
                reason_text = text[text_lower.index(prefix) + len(prefix):].strip()
                reason_text = reason_text.rstrip(".,!").strip()
                result["reason"] = reason_text
                return result

        # Check for known reason keywords directly
        reason_keywords = {
            "stranger": "Stranger", "someone": "Stranger", "person": "Stranger",
            "delivery": "Delivery", "postman": "Delivery", "mailman": "Delivery",
            "courier": "Delivery", "parcel": "Delivery", "package": "Delivery",
            "auspost": "Delivery", "amazon": "Delivery",
            "animal": "Animal", "cat": "Animal", "bird": "Animal",
            "squirrel": "Animal", "possum": "Animal", "shadow": "Animal",
            "dog": "Animal",
            "bored": "Boredom", "boredom": "Boredom", "nothing": "Boredom",
            "anxious": "Anxiety", "anxiety": "Anxiety", "scared": "Anxiety",
            "separation": "Anxiety",
            "doorbell": "Doorbell", "door": "Doorbell", "knock": "Doorbell",
            "ring": "Doorbell",
        }
        for keyword, reason in reason_keywords.items():
            if keyword in text_lower:
                result["reason"] = reason
                break

        # If nothing matched, treat the whole text as a comment
        if not result:
            result["comment"] = text.strip()

        return result

    def _parse_summary_range(self, text: str) -> tuple[str, str, str] | None:
        """Parse a summary command into (start_date, end_date, label).

        Returns None if text is not a summary command.
        Dates are YYYY-MM-DD strings. end_date is exclusive (day after last day).
        """
        tz = ZoneInfo(settings.timezone)
        now = datetime.now(tz)
        text_lower = text.lower().strip()

        if not text_lower.startswith("summary"):
            return None

        arg = text_lower[len("summary"):].strip()

        if not arg or arg == "today":
            start = now.strftime("%Y-%m-%d")
            end = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            return start, end, now.strftime("%A, %B %d")

        if arg == "yesterday":
            yesterday = now - timedelta(days=1)
            start = yesterday.strftime("%Y-%m-%d")
            end = now.strftime("%Y-%m-%d")
            return start, end, yesterday.strftime("%A, %B %d")

        if arg == "last week":
            # Previous Mon–Sun
            this_monday = now - timedelta(days=now.weekday())
            last_monday = this_monday - timedelta(days=7)
            last_sunday = this_monday - timedelta(days=1)
            start = last_monday.strftime("%Y-%m-%d")
            end = this_monday.strftime("%Y-%m-%d")
            return start, end, f"{last_monday.strftime('%b %d')} – {last_sunday.strftime('%b %d')}"

        if arg == "this week":
            # Monday to today (inclusive)
            monday = now - timedelta(days=now.weekday())
            start = monday.strftime("%Y-%m-%d")
            end = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            return start, end, f"{monday.strftime('%b %d')} – {now.strftime('%b %d')}"

        if arg == "this month":
            first = now.replace(day=1)
            start = first.strftime("%Y-%m-%d")
            end = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            return start, end, now.strftime("%B %Y")

        if arg == "last month":
            first_this_month = now.replace(day=1)
            last_month_end = first_this_month - timedelta(days=1)
            first_last_month = last_month_end.replace(day=1)
            start = first_last_month.strftime("%Y-%m-%d")
            end = first_this_month.strftime("%Y-%m-%d")
            return start, end, first_last_month.strftime("%B %Y")

        # Try a year (e.g. "2026")
        if arg.isdigit() and len(arg) == 4:
            year = int(arg)
            start = f"{year}-01-01"
            end = f"{year + 1}-01-01"
            return start, end, str(year)

        # Try a month name (e.g. "march", "march 2026", "feb")
        for month_fmt in ("%B %Y", "%b %Y", "%B", "%b"):
            try:
                parsed = datetime.strptime(arg, month_fmt)
                if parsed.year == 1900:
                    parsed = parsed.replace(year=now.year)
                last_day = calendar.monthrange(parsed.year, parsed.month)[1]
                start = parsed.strftime("%Y-%m-%d")
                end = (parsed.replace(day=last_day) + timedelta(days=1)).strftime("%Y-%m-%d")
                return start, end, parsed.strftime("%B %Y")
            except ValueError:
                continue

        # Try parsing a specific date
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%B %d", "%b %d", "%d %B", "%d %b"):
            try:
                parsed = datetime.strptime(arg, fmt)
                if parsed.year == 1900:
                    parsed = parsed.replace(year=now.year)
                start = parsed.strftime("%Y-%m-%d")
                end = (parsed + timedelta(days=1)).strftime("%Y-%m-%d")
                return start, end, parsed.strftime("%A, %B %d")
            except ValueError:
                continue

        return None

    def send_range_summary(self, episodes: list[dict], label: str, is_range: bool = False):
        """Send a summary for a date range."""
        tz = ZoneInfo(settings.timezone)

        if not episodes:
            text = (
                f"📋 *Bark Summary — {label}*\n\n"
                f"✅ No barking episodes detected! Good boy! 🐕"
            )
        else:
            total_bark_time = sum(e["bark_time_seconds"] for e in episodes)
            total_barks = sum(e["bark_count"] for e in episodes)
            if total_bark_time >= 60:
                total_str = f"{total_bark_time / 60:.0f}m {total_bark_time % 60:.0f}s"
            else:
                total_str = f"{total_bark_time:.0f}s"

            longest = max(episodes, key=lambda e: e["duration_seconds"])
            longest_dur = longest["duration_seconds"]
            if longest_dur >= 60:
                longest_str = f"{longest_dur / 60:.0f}m {longest_dur % 60:.0f}s"
            else:
                longest_str = f"{longest_dur:.0f}s"

            longest_time = longest["start_time"]
            longest_time_str = longest_time.astimezone(tz).strftime("%I:%M %p %a") if is_range else longest_time.astimezone(tz).strftime("%I:%M %p")
            if not longest_time.tzinfo:
                longest_time_str = longest_time.strftime("%I:%M %p")

            text = (
                f"📋 *Bark Summary — {label}*\n\n"
                f"📊 Total episodes: {len(episodes)}\n"
                f"🐕 Total barks: {total_barks}\n"
                f"⏱ Total bark time: {total_str}\n"
                f"🔝 Longest episode: {longest_str} at {longest_time_str}\n\n"
            )

            for i, ep in enumerate(episodes, 1):
                ep_time = ep["start_time"]
                if ep_time.tzinfo:
                    time_str = ep_time.astimezone(tz).strftime("%I:%M %p %a") if is_range else ep_time.astimezone(tz).strftime("%I:%M %p")
                else:
                    time_str = ep_time.strftime("%I:%M %p")
                ep_dur = f"{ep['duration_seconds']:.0f}s"
                if ep["duration_seconds"] >= 60:
                    ep_dur = f"{ep['duration_seconds'] / 60:.0f}m"
                bark_info = f"{ep['bark_count']} barks" if ep["bark_count"] else ep_dur
                cam = f" [{ep['camera']}]" if ep.get("camera") else ""
                text += f"{i}. {time_str} — {ep['bark_type']}{cam} ({bark_info})\n"

        self._send(
            "sendMessage",
            chat_id=self._chat_id,
            text=text,
            parse_mode="Markdown",
        )

    def _process_update(self, update: dict):
        """Process a single Telegram update."""
        message = update.get("message", {})
        user_id = message.get("from", {}).get("id")
        chat_id = message.get("chat", {}).get("id")

        if not user_id or not self._is_authorized(user_id):
            return

        text = message.get("text", "")

        # Check for summary command (not a reply)
        reply_to = message.get("reply_to_message", {})
        reply_msg_id = reply_to.get("message_id")

        if not reply_msg_id and self._notion:
            summary_range = self._parse_summary_range(text)
            if summary_range:
                start_date, end_date, label = summary_range
                is_range = start_date != (datetime.now(ZoneInfo(settings.timezone))).strftime("%Y-%m-%d") or "–" in label
                episodes = self._notion.get_episodes_for_range(start_date, end_date)
                self.send_range_summary(episodes, label, is_range="–" in label)
                return

        if reply_msg_id and self._notion:
            # Look up the Notion page by the Telegram message ID
            page_id = self._notion.find_page_by_message_id(reply_msg_id)
            if not page_id:
                return

            fields = self._parse_reply(text)
            confirmations = []

            if fields.get("file_request"):
                file_type = fields["file_request"]
                if self._on_file_request:
                    file_path = self._on_file_request(page_id, file_type)
                    if file_path:
                        self.send_file(file_path, file_type, reply_to_message_id=message.get("message_id"))
                    else:
                        self._send(
                            "sendMessage",
                            chat_id=self._chat_id,
                            text=f"❌ No {file_type} available for this event",
                            reply_to_message_id=message.get("message_id"),
                        )
                else:
                    self._send(
                        "sendMessage",
                        chat_id=self._chat_id,
                        text="❌ File retrieval not available",
                        reply_to_message_id=message.get("message_id"),
                    )
                return
            elif fields.get("not_bark"):
                self._notion.update_bark_type(page_id, "Not Bark")
                confirmations.append("✅ Marked as Not Bark")
            else:
                if fields.get("was_bark"):
                    self._notion.update_bark_type(page_id, "Bark")
                    confirmations.append("✅ Confirmed as Bark")

                # Process home/away, intervention, reason alongside was_bark
                intervention_fields = {}
                if "was_home" in fields:
                    intervention_fields["was_home"] = fields["was_home"]
                    if fields["was_home"]:
                        confirmations.append("✅ Marked as home")
                    else:
                        confirmations.append("✅ Marked as away")
                if fields.get("intervened"):
                    intervention_fields["intervened"] = True
                    confirmations.append("✅ Marked as intervened")
                if fields.get("reason"):
                    intervention_fields["reason"] = fields["reason"]
                    confirmations.append(f"✅ Reason: {fields['reason']}")

                if intervention_fields and self._on_intervention:
                    self._on_intervention(page_id, intervention_fields)

                # If nothing was parsed, treat as comment
                if not confirmations:
                    comment_text = text.strip()
                    self._notion.add_comment(page_id, comment_text)
                    confirmations.append("💬 Comment added")

            self._send(
                "sendMessage",
                chat_id=self._chat_id,
                text="\n".join(confirmations) if confirmations else "❓ Couldn't parse reply. Try: `not bark`, `home`, `reason: mailman`, or any comment",
                parse_mode="Markdown",
                reply_to_message_id=message.get("message_id"),
            )

    def start_polling(self):
        """Start polling for replies in a background thread."""
        if not self.enabled:
            logger.warning("Telegram bot not configured, skipping")
            return

        self._running = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        logger.info("Telegram bot polling started")

    def _poll_loop(self):
        """Long-poll for Telegram updates."""
        while self._running:
            try:
                params = {
                    "offset": self._last_update_id + 1,
                    "timeout": 30,
                    "allowed_updates": ["message"],
                }
                resp = self._client.post(
                    f"{self._base_url}/getUpdates",
                    json=params,
                    timeout=60,
                )
                resp.raise_for_status()
                updates = resp.json().get("result", [])

                for update in updates:
                    self._last_update_id = update["update_id"]
                    self._process_update(update)

            except Exception:
                logger.exception("Telegram poll error")
                time.sleep(5)

    def stop(self):
        self._running = False
