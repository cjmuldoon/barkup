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
                 on_file_request: callable = None, on_health_request: callable = None,
                 on_health_restart: callable = None):
        """
        Args:
            on_intervention: Callback(page_id, fields) for intervention updates.
            notion_logger: NotionLogger instance for looking up pages by message ID.
            on_file_request: Callback(page_id, file_type) -> file_path or None.
                           file_type is "clip", "video", or "snapshot".
            on_health_request: Callback() -> dict with health metrics.
            on_health_restart: Callback() to reset health timer and reconnect stream.
        """
        self._token = settings.telegram_bot_token
        self._chat_id = settings.telegram_chat_id
        self._base_url = TELEGRAM_API.format(token=self._token)
        self._client = httpx.Client(timeout=30)
        self._on_intervention = on_intervention
        self._on_file_request = on_file_request
        self._on_health_request = on_health_request
        self._on_health_restart = on_health_restart
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
        self._owner_home = False  # Persistent home state, toggled via "home"/"not home"

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._chat_id)

    @property
    def owner_home(self) -> bool:
        return self._owner_home

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

    def send_nest_only_notification(self, timestamp: datetime, camera_name: str | None = None,
                                       nest_link: str | None = None) -> int | None:
        """Send a notification for a Nest-only sound event (outside monitoring hours)."""
        tz = ZoneInfo(settings.timezone)
        local_start = timestamp.astimezone(tz) if timestamp.tzinfo else timestamp.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
        local_time = local_start.strftime("%I:%M:%S %p")

        cam_line = f"📷 Camera: {camera_name}\n" if camera_name else ""
        text = (
            f"🔔 *Nest Sound Event*\n\n"
            f"{cam_line}"
            f"⏰ Time: {local_time}\n"
            f"🔍 Source: Nest only (outside monitoring hours)\n"
        )

        if nest_link:
            text += f"\n[View in Nest]({nest_link})"

        text += (
            f"\n\n_Reply to update:_\n"
            f"e.g. `was bark`, `not bark`, or any comment"
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
            f"👍 bark  👎 not bark  |  or reply with details"
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
        home_line = "🏠 Owner: Home\n" if self._owner_home else ""
        text = (
            f"🐕 *Bark Detected*\n\n"
            f"{cam_line}"
            f"⏰ Time: {local_time}\n"
            f"⏱ Duration: {dur_str} ({bark_str} barking, {episode.bark_frame_count} barks)\n"
            f"📊 Confidence: {confidence_pct:.0f}%\n"
            f"🔊 Type: {episode.dominant_bark_type.value}\n"
            f"{source_line}"
            f"{home_line}"
        )

        if episode.nest_link:
            text += f"\n[View in Nest]({episode.nest_link})"

        text += (
            f"\n\n_Reply to update:_\n"
            f"👍 bark  👎 not bark  |  or reply with details"
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

    def _categorise_episodes(self, episodes: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
        """Split episodes into (confirmed_barks, not_barks, unconfirmed)."""
        confirmed = []
        not_barks = []
        unconfirmed = []
        for ep in episodes:
            bt = ep.get("bark_type", "").lower()
            if bt == "unconfirmed":
                unconfirmed.append(ep)
            elif bt in ("not bark", "not a bark", "false positive"):
                not_barks.append(ep)
            else:
                confirmed.append(ep)
        return confirmed, not_barks, unconfirmed

    def _eddie_verdict(self, confirmed_count: int, longest_seconds: float, total_bark_time: float) -> str:
        """Generate a playful Eddie verdict based on bark stats."""
        import random

        if confirmed_count == 0:
            lines = [
                "🏆 Eddie Dean: Model citizen. Karen in shambles.",
                "😇 Eddie didn't make a peep. Somebody check he's breathing.",
                "🥇 Zero barks. Eddie for Neighbour of the Year.",
                "✨ Silence from Eddie Dean. Shadow the cat remains the real menace.",
                "👼 Not a single bark. Eddie's lawyer rests their case.",
                "🧘 Eddie achieved inner peace today. Namaste, good boy.",
                "🎖️ Eddie's disciplinary record: spotless. Karen's letter: debunked.",
                "🕊️ Eddie kept the peace. The suburb sleeps soundly tonight.",
            ]
        elif confirmed_count <= 3 and longest_seconds < 60:
            lines = [
                f"😊 {confirmed_count} little bark{'s' if confirmed_count > 1 else ''}. Eddie's practically a monk.",
                f"🐾 Just {confirmed_count} bark{'s' if confirmed_count > 1 else ''}. Barely a whisper by Groodle standards.",
                f"👍 {confirmed_count} bark{'s' if confirmed_count > 1 else ''}, longest {longest_seconds:.0f}s. That's not barking, that's commentary.",
                f"😌 {confirmed_count} bark{'s' if confirmed_count > 1 else ''} all day? Shadow the cat causes more noise walking across the roof.",
                f"🐕 {confirmed_count} tiny outburst{'s' if confirmed_count > 1 else ''}. Eddie showed remarkable restraint.",
                f"📝 Dear Karen: {confirmed_count} bark{'s' if confirmed_count > 1 else ''}, {longest_seconds:.0f}s max. Please update your records.",
            ]
        elif confirmed_count <= 10 or (longest_seconds < 180 and total_bark_time < 300):
            lines = [
                f"😏 {confirmed_count} episodes. Eddie had opinions today, but kept them brief.",
                f"🗣️ {confirmed_count} barks, longest {longest_seconds:.0f}s. Eddie was chatty but not unreasonable.",
                f"📊 {confirmed_count} episodes. Still less noise than Karen's letter-writing sessions.",
                f"🤷 {confirmed_count} barks. In Eddie's defence, things probably needed barking at.",
                f"⚖️ {confirmed_count} episodes, {self._format_duration(total_bark_time)} total. The jury says: normal dog behaviour.",
                f"🐶 {confirmed_count} episodes. Eddie's not perfect, but he's no 2-hour menace either.",
            ]
        elif confirmed_count <= 20 or longest_seconds < 600:
            lines = [
                f"😬 {confirmed_count} episodes, longest {self._format_duration(longest_seconds)}. Eddie was feeling spicy today.",
                f"🌶️ {confirmed_count} episodes. Eddie chose violence (verbal).",
                f"📢 {confirmed_count} episodes, {self._format_duration(total_bark_time)} of barking. Eddie had a lot to say.",
                f"🎤 {confirmed_count} barking episodes. Eddie's audition for neighbourhood watch went long.",
                f"😅 {confirmed_count} episodes. Eddie, mate, let's dial it back tomorrow.",
                f"🔊 {confirmed_count} episodes today. Eddie's defence lawyer is sweating a little.",
            ]
        else:
            lines = [
                f"🚨 {confirmed_count} episodes, longest {self._format_duration(longest_seconds)}. Eddie. Mate. We need to talk.",
                f"💀 {confirmed_count} episodes, {self._format_duration(total_bark_time)} of barking. Karen might have a point today.",
                f"📣 {confirmed_count} episodes. Eddie went full neighbourhood broadcast system.",
                f"😱 Longest bark: {self._format_duration(longest_seconds)}. Eddie was channelling his inner wolf.",
                f"🙈 {confirmed_count} episodes. Even Eddie's lawyer is looking at the evidence nervously.",
                f"🔥 {self._format_duration(total_bark_time)} of barking. Tomorrow's a new day, Eddie.",
            ]

        return random.choice(lines)

    def _format_duration(self, seconds: float) -> str:
        if seconds >= 60:
            return f"{seconds / 60:.0f}m {seconds % 60:.0f}s"
        return f"{seconds:.0f}s"

    def _build_summary_header(self, label: str, episodes: list[dict], confirmed: list[dict],
                               not_barks: list[dict], unconfirmed: list[dict],
                               tz, is_range: bool = False) -> str:
        """Build the stats header for a summary message."""
        if not episodes:
            verdict = self._eddie_verdict(0, 0, 0)
            return (
                f"📋 *Bark Summary — {label}*\n\n"
                f"✅ No episodes detected!\n\n"
                f"{verdict}"
            )

        # Stats from confirmed barks only
        total_bark_time = sum(e["bark_time_seconds"] for e in confirmed)
        total_barks = sum(e["bark_count"] for e in confirmed)

        text = f"📋 *Bark Summary — {label}*\n\n"
        text += f"🐕 Confirmed barks: {len(confirmed)}"
        if not_barks:
            text += f"  |  ❌ Not barks: {len(not_barks)}"
        if unconfirmed:
            text += f"  |  ❓ Unconfirmed: {len(unconfirmed)}"
        text += "\n"

        longest_seconds = 0
        if confirmed:
            text += f"🔢 Total bark count: {total_barks}\n"
            text += f"⏱ Total bark time: {self._format_duration(total_bark_time)}\n"

            longest = max(confirmed, key=lambda e: e["duration_seconds"])
            longest_seconds = longest["duration_seconds"]
            longest_time = longest["start_time"]
            fmt = "%I:%M %p %a" if is_range else "%I:%M %p"
            longest_time_str = longest_time.astimezone(tz).strftime(fmt) if longest_time.tzinfo else longest_time.strftime("%I:%M %p")
            text += f"🔝 Longest bark: {self._format_duration(longest_seconds)} at {longest_time_str}\n"

        text += f"\n{self._eddie_verdict(len(confirmed), longest_seconds, total_bark_time)}\n\n"
        return text

    def _build_episode_list(self, episodes: list[dict], tz, is_range: bool = False) -> str:
        """Build the numbered episode list."""
        text = ""
        for i, ep in enumerate(episodes, 1):
            ep_time = ep["start_time"]
            fmt = "%I:%M %p %a" if is_range else "%I:%M %p"
            time_str = ep_time.astimezone(tz).strftime(fmt) if ep_time.tzinfo else ep_time.strftime("%I:%M %p")
            ep_dur = self._format_duration(ep["duration_seconds"]) if ep["duration_seconds"] else ""
            bark_info = f"{ep['bark_count']} barks" if ep["bark_count"] else ep_dur
            cam = f" [{ep['camera']}]" if ep.get("camera") else ""
            text += f"{i}. {time_str} — {ep['bark_type']}{cam} ({bark_info})\n"
        return text

    def send_nightly_summary(self, episodes: list[dict], date: datetime = None):
        """Send the nightly summary at 8pm. Episodes are dicts from Notion query."""
        tz = ZoneInfo(settings.timezone)
        date = date or datetime.now(tz)
        date_str = date.strftime("%A, %B %d")

        confirmed, not_barks, unconfirmed = self._categorise_episodes(episodes)
        text = self._build_summary_header(date_str, episodes, confirmed, not_barks, unconfirmed, tz)
        if episodes:
            text += self._build_episode_list(episodes, tz)

        self._send(
            "sendMessage",
            chat_id=self._chat_id,
            text=text,
            parse_mode="Markdown",
        )

    def send_health_check(self, health: dict):
        """Send a system health check message.

        health dict keys: uptime_hours, frames_processed, frames_expected,
                         processing_pct, disk_used_mb, disk_total_mb, clip_count, clip_size_mb,
                         measure_since (datetime or None)
        """
        processing_pct = health.get("processing_pct", 0)
        status = "✅" if processing_pct >= 95 else "⚠️" if processing_pct >= 80 else "🔴"

        text = f"🔧 *System Health Check*\n\n"
        text += f"{status} Processing: {processing_pct:.0f}% real-time"
        text += f" ({health.get('frames_processed', 0)} frames in {health.get('uptime_hours', 0):.1f}h)\n"

        measure_since = health.get("measure_since")
        if measure_since:
            text += f"📏 Measuring since: {measure_since.strftime('%I:%M %p')}\n"
        else:
            text += f"📏 Measuring since: awaiting first frame\n"

        text += f"💾 Disk: {health.get('disk_used_mb', 0):.0f}MB / {health.get('disk_total_mb', 0):.0f}MB"
        disk_pct = (health.get('disk_used_mb', 0) / health.get('disk_total_mb', 1)) * 100
        disk_icon = "✅" if disk_pct < 80 else "⚠️" if disk_pct < 90 else "🔴"
        text += f" ({disk_pct:.0f}%) {disk_icon}\n"
        text += f"📁 Clips: {health.get('clip_count', 0)} files, {health.get('clip_size_mb', 0):.0f}MB\n"

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
                           "real bark", "yes bark", "was barking", "genuine", "real",
                           "barked", "dog barked", "he barked", "she barked"]
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

        # Check for known reason keywords directly.
        # Only match short, keyword-style replies (up to 5 words) to avoid
        # false matches in longer commentary like "Tony doing something next door".
        import re
        words = text_lower.split()
        reason_keywords = {
            "stranger": "Stranger", "someone": "Stranger", "person": "Stranger",
            "delivery": "Delivery", "postman": "Delivery", "mailman": "Delivery",
            "courier": "Delivery", "parcel": "Delivery", "package": "Delivery",
            "auspost": "Delivery", "amazon": "Delivery",
            "animal": "Animal", "cat": "Animal", "bird": "Animal",
            "squirrel": "Animal", "possum": "Animal", "shadow": "Animal",
            "bored": "Boredom", "boredom": "Boredom", "nothing": "Boredom",
            "anxious": "Anxiety", "anxiety": "Anxiety", "scared": "Anxiety",
            "separation": "Anxiety",
            "doorbell": "Doorbell", "knock": "Doorbell",
        }
        if len(words) <= 5:
            for keyword, reason in reason_keywords.items():
                # Match whole words only
                if re.search(rf'\b{keyword}\b', text_lower):
                    result["reason"] = reason
                    break

        # If nothing matched, treat the whole text as a comment
        if not result:
            result["comment"] = text.strip()

        return result

    def _parse_summary_range(self, text: str) -> tuple[str, str, str, str] | None:
        """Parse a summary command into (start_date, end_date, label, granularity).

        Returns None if text is not a summary command.
        Dates are YYYY-MM-DD strings. end_date is exclusive (day after last day).
        Granularity: "day" (flat list), "weekly" (daily breakdown),
                     "monthly" (weekly breakdown), "yearly" (monthly breakdown).
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
            return start, end, now.strftime("%A, %B %d"), "day"

        if arg == "yesterday":
            yesterday = now - timedelta(days=1)
            start = yesterday.strftime("%Y-%m-%d")
            end = now.strftime("%Y-%m-%d")
            return start, end, yesterday.strftime("%A, %B %d"), "day"

        if arg == "last week":
            this_monday = now - timedelta(days=now.weekday())
            last_monday = this_monday - timedelta(days=7)
            last_sunday = this_monday - timedelta(days=1)
            start = last_monday.strftime("%Y-%m-%d")
            end = this_monday.strftime("%Y-%m-%d")
            return start, end, f"{last_monday.strftime('%b %d')} – {last_sunday.strftime('%b %d')}", "weekly"

        if arg in ("this week", "week", "weekly"):
            monday = now - timedelta(days=now.weekday())
            start = monday.strftime("%Y-%m-%d")
            end = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            return start, end, f"{monday.strftime('%b %d')} – {now.strftime('%b %d')}", "weekly"

        if arg in ("this month", "month", "monthly"):
            first = now.replace(day=1)
            start = first.strftime("%Y-%m-%d")
            end = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            return start, end, now.strftime("%B %Y"), "monthly"

        if arg == "last month":
            first_this_month = now.replace(day=1)
            last_month_end = first_this_month - timedelta(days=1)
            first_last_month = last_month_end.replace(day=1)
            start = first_last_month.strftime("%Y-%m-%d")
            end = first_this_month.strftime("%Y-%m-%d")
            return start, end, first_last_month.strftime("%B %Y"), "monthly"

        if arg in ("this year", "year", "yearly"):
            start = f"{now.year}-01-01"
            end = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            return start, end, str(now.year), "yearly"

        if arg == "last year":
            start = f"{now.year - 1}-01-01"
            end = f"{now.year}-01-01"
            return start, end, str(now.year - 1), "yearly"

        # Try a year (e.g. "2026")
        if arg.isdigit() and len(arg) == 4:
            year = int(arg)
            start = f"{year}-01-01"
            end = f"{year + 1}-01-01"
            return start, end, str(year), "yearly"

        # Try a month name (e.g. "march", "march 2026", "feb")
        for month_fmt in ("%B %Y", "%b %Y", "%B", "%b"):
            try:
                parsed = datetime.strptime(arg, month_fmt)
                if parsed.year == 1900:
                    parsed = parsed.replace(year=now.year)
                last_day = calendar.monthrange(parsed.year, parsed.month)[1]
                start = parsed.strftime("%Y-%m-%d")
                end = (parsed.replace(day=last_day) + timedelta(days=1)).strftime("%Y-%m-%d")
                return start, end, parsed.strftime("%B %Y"), "monthly"
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
                return start, end, parsed.strftime("%A, %B %d"), "day"
            except ValueError:
                continue

        return None

    def _group_episodes_by(self, episodes: list[dict], key_fn, tz) -> dict:
        """Group episodes into buckets using key_fn(episode, tz) -> key."""
        groups = {}
        for ep in episodes:
            k = key_fn(ep, tz)
            groups.setdefault(k, []).append(ep)
        return groups

    def _format_sub_period_line(self, label: str, episodes: list[dict]) -> str:
        """Format a single sub-period line with stats from confirmed barks only."""
        confirmed, not_barks, unconfirmed = self._categorise_episodes(episodes)
        bark_count = len(confirmed)
        bark_time = sum(e["bark_time_seconds"] for e in confirmed)
        time_str = self._format_duration(bark_time) if bark_time else "0s"
        parts = [f"{bark_count} bark{'s' if bark_count != 1 else ''}"]
        if bark_time:
            parts.append(time_str)
        if not_barks:
            parts.append(f"{len(not_barks)} false")
        if unconfirmed:
            parts.append(f"{len(unconfirmed)} unconfirmed")
        return f"  {label}: {' | '.join(parts)}\n"

    def _build_grouped_summary(self, episodes: list[dict], label: str,
                                group_key_fn, group_label_fn, tz) -> str:
        """Build a summary with sub-period breakdown."""
        confirmed, not_barks, unconfirmed = self._categorise_episodes(episodes)
        text = self._build_summary_header(label, episodes, confirmed, not_barks, unconfirmed, tz, is_range=True)

        if episodes:
            groups = self._group_episodes_by(episodes, group_key_fn, tz)
            for key in sorted(groups.keys()):
                group_label = group_label_fn(key, tz)
                text += self._format_sub_period_line(group_label, groups[key])

        return text

    def send_range_summary(self, episodes: list[dict], label: str,
                           is_range: bool = False, granularity: str = "day"):
        """Send a summary for a date range.

        granularity: "day" (flat list), "weekly" (daily breakdown),
                     "monthly" (weekly breakdown), "yearly" (monthly breakdown)
        """
        tz = ZoneInfo(settings.timezone)

        if granularity == "weekly":
            # Group by day
            text = self._build_grouped_summary(
                episodes, label,
                group_key_fn=lambda ep, tz: ep["start_time"].astimezone(tz).strftime("%Y-%m-%d"),
                group_label_fn=lambda k, tz: datetime.strptime(k, "%Y-%m-%d").strftime("%a %b %d"),
                tz=tz,
            )
        elif granularity == "monthly":
            # Group by week number
            def week_key(ep, tz):
                dt = ep["start_time"].astimezone(tz)
                # Start of week (Monday)
                monday = dt - timedelta(days=dt.weekday())
                return monday.strftime("%Y-%m-%d")

            def week_label(k, tz):
                monday = datetime.strptime(k, "%Y-%m-%d")
                sunday = monday + timedelta(days=6)
                return f"{monday.strftime('%b %d')}–{sunday.strftime('%d')}"

            text = self._build_grouped_summary(
                episodes, label,
                group_key_fn=week_key,
                group_label_fn=week_label,
                tz=tz,
            )
        elif granularity == "yearly":
            # Group by month
            text = self._build_grouped_summary(
                episodes, label,
                group_key_fn=lambda ep, tz: ep["start_time"].astimezone(tz).strftime("%Y-%m"),
                group_label_fn=lambda k, tz: datetime.strptime(k, "%Y-%m").strftime("%B"),
                tz=tz,
            )
        else:
            # Flat episode list (daily or small ranges)
            confirmed, not_barks, unconfirmed = self._categorise_episodes(episodes)
            text = self._build_summary_header(label, episodes, confirmed, not_barks, unconfirmed, tz, is_range)
            if episodes:
                text += self._build_episode_list(episodes, tz, is_range)

        self._send(
            "sendMessage",
            chat_id=self._chat_id,
            text=text,
            parse_mode="Markdown",
        )

    def _process_reaction(self, update: dict):
        """Process a message reaction (thumbs up/down)."""
        reaction = update.get("message_reaction", {})
        user_id = reaction.get("user", {}).get("id")
        if not user_id or not self._is_authorized(user_id):
            return

        message_id = reaction.get("message_id")
        new_reactions = reaction.get("new_reaction", [])
        if not message_id or not new_reactions:
            return

        emoji = new_reactions[0].get("emoji", "")
        logger.info("Telegram reaction from %s: %s on message %s", user_id, emoji, message_id)

        if emoji not in ("👍", "👎"):
            return

        if not self._notion:
            return

        page_id = self._notion.find_page_by_message_id(message_id)
        if not page_id:
            return

        if emoji == "👍":
            self._notion.update_bark_type(page_id, "Bark")
            self._send("sendMessage", chat_id=self._chat_id,
                       text="✅ Confirmed as Bark",
                       reply_to_message_id=message_id)
            logger.info("👍 reaction: confirmed bark for page %s", page_id)
        elif emoji == "👎":
            self._notion.update_bark_type(page_id, "Not Bark")
            self._send("sendMessage", chat_id=self._chat_id,
                       text="✅ Marked as Not Bark",
                       reply_to_message_id=message_id)
            logger.info("👎 reaction: marked not bark for page %s", page_id)

    def _process_update(self, update: dict):
        """Process a single Telegram update."""
        # Handle reactions
        if "message_reaction" in update:
            self._process_reaction(update)
            return

        message = update.get("message", {})
        user_id = message.get("from", {}).get("id")
        chat_id = message.get("chat", {}).get("id")

        if not user_id or not self._is_authorized(user_id):
            return

        text = message.get("text", "")
        logger.info("Telegram message from %s: %r (reply_to: %s)", user_id, text,
                     message.get("reply_to_message", {}).get("message_id"))

        # Check for standalone commands (not replies)
        reply_to = message.get("reply_to_message", {})
        reply_msg_id = reply_to.get("message_id")

        if not reply_msg_id:
            text_lower = text.lower().strip()

            # Health restart command — reset timer + reconnect stream
            if text_lower in ("health restart", "restart health", "reset health"):
                if self._on_health_restart:
                    self._on_health_restart()
                    self._send("sendMessage", chat_id=self._chat_id,
                               text="🔄 Health timer reset and stream reconnecting. Send `health` in a minute to see fresh stats.",
                               parse_mode="Markdown")
                else:
                    self._send("sendMessage", chat_id=self._chat_id,
                               text="❌ Health restart not available")
                return

            # Health check command
            if text_lower in ("health", "health check", "status"):
                if self._on_health_request:
                    health = self._on_health_request()
                    self.send_health_check(health)
                else:
                    self._send("sendMessage", chat_id=self._chat_id,
                               text="❌ Health check not available")
                return

            # Home/not home toggle (as general message)
            away_phrases = ["not home", "not at home", "leaving", "left home", "going out"]
            if any(phrase in text_lower for phrase in away_phrases):
                self._owner_home = False
                self._send("sendMessage", chat_id=self._chat_id,
                           text="🚪 Marked as *not home*. Episodes will no longer be auto-marked.",
                           parse_mode="Markdown")
                return

            home_phrases = ["home", "i'm home", "im home", "got home", "back home"]
            if any(phrase == text_lower or text_lower.startswith(phrase) for phrase in home_phrases):
                self._owner_home = True
                self._send("sendMessage", chat_id=self._chat_id,
                           text="🏠 Marked as *home*. All episodes will be auto-marked as home until you say 'not home'.",
                           parse_mode="Markdown")
                return

        if not reply_msg_id and self._notion:
            summary_result = self._parse_summary_range(text)
            if summary_result:
                start_date, end_date, label, granularity = summary_result
                episodes = self._notion.get_episodes_for_range(start_date, end_date)
                self.send_range_summary(episodes, label, is_range=granularity != "day", granularity=granularity)
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
                        self._owner_home = True
                        confirmations.append("✅ Marked as home (auto-marking on)")
                    else:
                        self._owner_home = False
                        confirmations.append("✅ Marked as away (auto-marking off)")
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
                    "allowed_updates": ["message", "message_reaction"],
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
