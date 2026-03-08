"""Logs bark episodes to Notion database."""

import logging
from datetime import timezone
from zoneinfo import ZoneInfo

from notion_client import Client

from barkup.config import settings
from barkup.models import Episode

logger = logging.getLogger(__name__)


class NotionLogger:
    def __init__(self):
        self._client = Client(auth=settings.notion_api_key)
        self._database_id = settings.notion_database_id

    def log_episode(self, episode: Episode) -> str:
        """
        Create a new page in the Notion database for a bark episode.
        Returns the created page URL.
        """
        tz = ZoneInfo(settings.timezone)
        # Ensure timestamps have timezone info
        start = episode.start_time if episode.start_time.tzinfo else episode.start_time.replace(tzinfo=ZoneInfo("UTC"))
        end = episode.end_time if episode.end_time.tzinfo else episode.end_time.replace(tzinfo=ZoneInfo("UTC"))
        start_iso = start.isoformat()
        end_iso = end.isoformat()

        # Format a readable event title in local time
        local_time = start.astimezone(tz).strftime("%I:%M %p")
        duration_min = episode.duration_seconds / 60
        cam_prefix = f"[{episode.camera_name}] " if episode.camera_name else ""
        if duration_min >= 1:
            title = f"{cam_prefix}{episode.dominant_bark_type.value} - {local_time} ({duration_min:.0f}m)"
        else:
            title = f"{cam_prefix}{episode.dominant_bark_type.value} - {local_time} ({episode.duration_seconds:.0f}s)"

        properties = {
            "Event": {"title": [{"text": {"content": title}}]},
            "Date/Time": {
                "date": {
                    "start": start_iso,
                    "end": end_iso,
                }
            },
            "Duration (sec)": {"number": episode.duration_seconds},
            "Bark Time (sec)": {"number": round(episode.bark_frame_count * 0.975, 1)},
            "Bark Count": {"number": episode.bark_frame_count},
            "Confidence": {"number": episode.peak_confidence},
            "Bark Type": {"select": {"name": episode.dominant_bark_type.value}},
            "Reason": {"select": {"name": "Unknown"}},
            "Owner Home": {"checkbox": False},
            "Intervened": {"checkbox": False},
        }

        if episode.camera_name:
            properties["Camera"] = {"select": {"name": episode.camera_name}}

        # Add optional URL fields
        if episode.clip_url:
            properties["Clip Link"] = {"url": episode.clip_url}
        if episode.nest_link:
            properties["Nest Link"] = {"url": episode.nest_link}
        if episode.clip_path:
            properties["Notes"] = {
                "rich_text": [
                    {"text": {"content": f"Local clip: {episode.clip_path}"}}
                ]
            }

        page = self._client.pages.create(
            parent={"database_id": self._database_id},
            properties=properties,
        )

        page_id = page.get("id", "")
        page_url = page.get("url", "")
        logger.info("Logged episode to Notion: %s", page_url)
        return page_id

    def update_intervention(self, page_id: str, fields: dict):
        """Update a Notion page with intervention details from Telegram reply."""
        properties = {}

        if fields.get("was_home"):
            properties["Owner Home"] = {"checkbox": True}
        if fields.get("intervened"):
            properties["Intervened"] = {"checkbox": True}
        if fields.get("reason"):
            # Map common reasons to select options
            reason_map = {
                "stranger": "Stranger",
                "animal": "Animal",
                "boredom": "Boredom",
                "anxiety": "Anxiety",
                "doorbell": "Doorbell",
            }
            reason_text = fields["reason"].lower()
            matched = reason_map.get(reason_text, "Other")
            properties["Reason"] = {"select": {"name": matched}}
            # Also add the raw reason to notes if it's custom
            if matched == "Other":
                properties["Notes"] = {
                    "rich_text": [
                        {"text": {"content": f"Reason: {fields['reason']}"}}
                    ]
                }

        if properties:
            self._client.pages.update(page_id=page_id, properties=properties)
            logger.info("Updated intervention for page %s", page_id)
