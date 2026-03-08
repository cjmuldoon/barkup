"""Logs bark episodes to Notion database."""

import logging
from datetime import timezone

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
        start_iso = episode.start_time.astimezone(timezone.utc).isoformat()
        end_iso = episode.end_time.astimezone(timezone.utc).isoformat()

        # Format a readable event title
        local_time = episode.start_time.strftime("%I:%M %p")
        duration_min = episode.duration_seconds / 60
        if duration_min >= 1:
            title = f"{episode.dominant_bark_type.value} - {local_time} ({duration_min:.0f}m)"
        else:
            title = f"{episode.dominant_bark_type.value} - {local_time} ({episode.duration_seconds:.0f}s)"

        properties = {
            "Event": {"title": [{"text": {"content": title}}]},
            "Date/Time": {
                "date": {
                    "start": start_iso,
                    "end": end_iso,
                }
            },
            "Duration (sec)": {"number": episode.duration_seconds},
            "Confidence": {"number": episode.peak_confidence},
            "Bark Type": {"select": {"name": episode.dominant_bark_type.value}},
            "Reason": {"select": {"name": "Unknown"}},
            "Owner Home": {"checkbox": False},
            "Intervened": {"checkbox": False},
        }

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

        page_url = page.get("url", "")
        logger.info("Logged episode to Notion: %s", page_url)
        return page_url
