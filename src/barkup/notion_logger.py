"""Logs bark episodes to Notion database."""

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from notion_client import Client

from barkup.config import settings
from barkup.models import Episode

logger = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionLogger:
    def __init__(self):
        self._client = Client(auth=settings.notion_api_key)
        self._database_id = settings.notion_database_id
        self._http = httpx.Client(
            base_url=NOTION_API,
            headers={
                "Authorization": f"Bearer {settings.notion_api_key}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            timeout=30,
        )

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

    def set_telegram_message_id(self, page_id: str, message_id: int):
        """Store the Telegram message ID on a Notion page for reply tracking."""
        self._client.pages.update(
            page_id=page_id,
            properties={"Telegram Message ID": {"number": message_id}},
        )

    def _query_database(self, filter: dict, sorts: list | None = None, page_size: int = 100) -> list[dict]:
        """Query the Notion database using the REST API directly."""
        body = {"filter": filter, "page_size": page_size}
        if sorts:
            body["sorts"] = sorts
        resp = self._http.post(
            f"/databases/{self._database_id}/query",
            json=body,
        )
        resp.raise_for_status()
        return resp.json().get("results", [])

    def find_page_by_message_id(self, message_id: int) -> str | None:
        """Look up a Notion page by its Telegram message ID."""
        pages = self._query_database(
            filter={
                "property": "Telegram Message ID",
                "number": {"equals": message_id},
            },
            page_size=1,
        )
        if pages:
            return pages[0]["id"]
        return None

    def get_today_episodes(self) -> list[dict]:
        """Query today's bark episodes from Notion for the nightly summary."""
        tz = ZoneInfo(settings.timezone)
        today = datetime.now(tz).strftime("%Y-%m-%d")

        result = self._query_database(
            filter={
                "property": "Date/Time",
                "date": {"on_or_after": today},
            },
            sorts=[{"property": "Date/Time", "direction": "ascending"}],
        )

        episodes = []
        for page in result:
            props = page["properties"]
            date_prop = props.get("Date/Time", {}).get("date", {})
            start_str = date_prop.get("start")
            if not start_str:
                continue

            start = datetime.fromisoformat(start_str)
            title_parts = props.get("Event", {}).get("title", [])
            title = title_parts[0]["text"]["content"] if title_parts else ""
            duration = props.get("Duration (sec)", {}).get("number", 0) or 0
            bark_time = props.get("Bark Time (sec)", {}).get("number", 0) or 0
            bark_count = props.get("Bark Count", {}).get("number", 0) or 0
            bark_type_sel = props.get("Bark Type", {}).get("select")
            bark_type = bark_type_sel["name"] if bark_type_sel else "Bark"
            camera_sel = props.get("Camera", {}).get("select")
            camera = camera_sel["name"] if camera_sel else None

            episodes.append({
                "title": title,
                "start_time": start,
                "duration_seconds": duration,
                "bark_time_seconds": bark_time,
                "bark_count": bark_count,
                "bark_type": bark_type,
                "camera": camera,
            })

        return episodes
