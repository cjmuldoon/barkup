"""Telegram bot for bark notifications and intervention tracking."""

import json
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
    def __init__(self, on_intervention: callable = None):
        """
        Args:
            on_intervention: Callback(episode_page_id, was_home, intervened, reason)
                called when user replies with intervention details.
        """
        self._token = settings.telegram_bot_token
        self._chat_id = settings.telegram_chat_id
        self._base_url = TELEGRAM_API.format(token=self._token)
        self._client = httpx.Client(timeout=30)
        self._on_intervention = on_intervention
        # Allowed user IDs (owner + anyone they add)
        allowed = settings.telegram_allowed_users
        self._allowed_users: set[str] = set()
        if allowed:
            self._allowed_users = {uid.strip() for uid in allowed.split(",")}
        if self._chat_id:
            self._allowed_users.add(str(self._chat_id))
        self._poll_thread = None
        self._running = False
        # Track message_id -> notion_page_id for reply handling
        self._message_to_page: dict[int, str] = {}
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

    def send_bark_notification(self, episode: Episode, notion_page_id: str = None) -> int | None:
        """Send a bark episode notification. Returns the message ID."""
        duration = episode.duration_seconds
        if duration >= 60:
            dur_str = f"{duration / 60:.0f}m {duration % 60:.0f}s"
        else:
            dur_str = f"{duration:.0f}s"

        tz = ZoneInfo(settings.timezone)
        local_start = episode.start_time.astimezone(tz) if episode.start_time.tzinfo else episode.start_time.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
        local_time = local_start.strftime("%I:%M:%S %p")
        confidence_pct = episode.peak_confidence * 100

        cam_line = f"📷 Camera: {episode.camera_name}\n" if episode.camera_name else ""
        text = (
            f"🐕 *Bark Detected*\n\n"
            f"{cam_line}"
            f"⏰ Time: {local_time}\n"
            f"⏱ Duration: {dur_str}\n"
            f"📊 Confidence: {confidence_pct:.0f}%\n"
            f"🔊 Type: {episode.dominant_bark_type.value}\n"
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

        if result and notion_page_id:
            msg_id = result.get("message_id")
            self._message_to_page[msg_id] = notion_page_id
            return msg_id
        return None

    def send_nightly_summary(self, episodes: list[Episode], date: datetime = None):
        """Send the nightly summary at 8pm."""
        tz = ZoneInfo(settings.timezone)
        date = date or datetime.now(tz)
        date_str = date.strftime("%A, %B %d")

        if not episodes:
            text = (
                f"📋 *Daily Bark Summary - {date_str}*\n\n"
                f"✅ No barking episodes detected today! Good boy! 🐕"
            )
        else:
            total_duration = sum(e.duration_seconds for e in episodes)
            if total_duration >= 60:
                total_str = f"{total_duration / 60:.0f}m {total_duration % 60:.0f}s"
            else:
                total_str = f"{total_duration:.0f}s"

            longest = max(episodes, key=lambda e: e.duration_seconds)
            longest_dur = longest.duration_seconds
            if longest_dur >= 60:
                longest_str = f"{longest_dur / 60:.0f}m {longest_dur % 60:.0f}s"
            else:
                longest_str = f"{longest_dur:.0f}s"

            text = (
                f"📋 *Daily Bark Summary - {date_str}*\n\n"
                f"📊 Total episodes: {len(episodes)}\n"
                f"⏱ Total bark time: {total_str}\n"
                f"🔝 Longest episode: {longest_str} "
                f"at {longest.start_time.astimezone(tz).strftime('%I:%M %p') if longest.start_time.tzinfo else longest.start_time.strftime('%I:%M %p')}\n\n"
            )

            # List each episode
            for i, ep in enumerate(episodes, 1):
                ep_time = ep.start_time.astimezone(tz).strftime("%I:%M %p") if ep.start_time.tzinfo else ep.start_time.strftime("%I:%M %p")
                ep_dur = f"{ep.duration_seconds:.0f}s"
                if ep.duration_seconds >= 60:
                    ep_dur = f"{ep.duration_seconds / 60:.0f}m"
                text += f"{i}. {ep_time} - {ep.dominant_bark_type.value} ({ep_dur})\n"

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
            "home"
            "I was home and intervened"
            "yes I was home, it was the mailman"
            "home, intervened, reason: stranger at door"
            "intervened - doorbell"
            "boredom"
        """
        text_lower = text.lower().strip()
        result = {}

        # Check for "home" / "was home"
        if "home" in text_lower:
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
            "delivery": "Stranger", "postman": "Stranger", "mailman": "Stranger",
            "animal": "Animal", "cat": "Animal", "dog": "Animal", "bird": "Animal",
            "squirrel": "Animal", "possum": "Animal",
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

        return result

    def _process_update(self, update: dict):
        """Process a single Telegram update."""
        message = update.get("message", {})
        user_id = message.get("from", {}).get("id")
        chat_id = message.get("chat", {}).get("id")

        if not user_id or not self._is_authorized(user_id):
            return

        # Check if it's a reply to one of our notifications
        reply_to = message.get("reply_to_message", {})
        reply_msg_id = reply_to.get("message_id")
        text = message.get("text", "")

        if reply_msg_id and reply_msg_id in self._message_to_page:
            page_id = self._message_to_page[reply_msg_id]
            fields = self._parse_reply(text)

            if fields and self._on_intervention:
                self._on_intervention(page_id, fields)
                # Confirm to user
                confirmations = []
                if fields.get("was_home"):
                    confirmations.append("✅ Marked as home")
                if fields.get("intervened"):
                    confirmations.append("✅ Marked as intervened")
                if fields.get("reason"):
                    confirmations.append(f"✅ Reason: {fields['reason']}")

                self._send(
                    "sendMessage",
                    chat_id=self._chat_id,
                    text="\n".join(confirmations) if confirmations else "❓ Couldn't parse reply. Try: `home, intervened, reason: mailman`",
                    parse_mode="Markdown",
                    reply_to_message_id=message.get("message_id"),
                )
            else:
                self._send(
                    "sendMessage",
                    chat_id=self._chat_id,
                    text="❓ Couldn't parse reply. Try: `home, intervened, reason: mailman`",
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
