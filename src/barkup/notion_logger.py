"""Logs bark episodes to Notion database."""

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from notion_client import Client

from barkup.config import settings
from barkup.models import DetectionSource, Episode

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

    def log_preliminary(self, timestamp: datetime, camera_name: str | None = None,
                        snapshot_path: str | None = None, nest_link: str | None = None) -> str:
        """Create a preliminary Notion page for a Sound event. Returns page ID."""
        tz = ZoneInfo(settings.timezone)
        start = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=ZoneInfo("UTC"))
        local_time = start.astimezone(tz).strftime("%I:%M %p")
        cam_prefix = f"[{camera_name}] " if camera_name else ""
        title = f"{cam_prefix}Sound detected - {local_time} (analyzing...)"

        properties = {
            "Event": {"title": [{"text": {"content": title}}]},
            "Date/Time": {"date": {"start": start.isoformat()}},
            "Bark Type": {"select": {"name": "Unconfirmed"}},
            "Confidence": {"number": 0},
            "Reason": {"select": {"name": "Unknown"}},
            "Owner Home": {"checkbox": False},
            "Intervened": {"checkbox": False},
        }
        if camera_name:
            properties["Camera"] = {"select": {"name": camera_name}}
        if nest_link:
            properties["Nest Link"] = {"url": nest_link}

        page = self._client.pages.create(
            parent={"database_id": self._database_id},
            properties=properties,
        )
        page_id = page.get("id", "")
        logger.info("Preliminary entry created: %s", page_id)
        return page_id

    def update_episode(self, page_id: str, episode: Episode):
        """Update a preliminary page with confirmed episode details."""
        tz = ZoneInfo(settings.timezone)
        start = episode.start_time if episode.start_time.tzinfo else episode.start_time.replace(tzinfo=ZoneInfo("UTC"))
        end = episode.end_time if episode.end_time.tzinfo else episode.end_time.replace(tzinfo=ZoneInfo("UTC"))
        local_time = start.astimezone(tz).strftime("%I:%M %p")
        duration_min = episode.duration_seconds / 60
        cam_prefix = f"[{episode.camera_name}] " if episode.camera_name else ""
        if duration_min >= 1:
            title = f"{cam_prefix}{episode.dominant_bark_type.value} - {local_time} ({duration_min:.0f}m)"
        else:
            title = f"{cam_prefix}{episode.dominant_bark_type.value} - {local_time} ({episode.duration_seconds:.0f}s)"

        properties = {
            "Event": {"title": [{"text": {"content": title}}]},
            "Date/Time": {"date": {"start": start.isoformat(), "end": end.isoformat()}},
            "Duration (sec)": {"number": episode.duration_seconds},
            "Bark Time (sec)": {"number": round(episode.bark_frame_count * 0.975, 1)},
            "Bark Count": {"number": episode.bark_frame_count},
            "Confidence": {"number": episode.peak_confidence},
            "Bark Type": {"select": {"name": episode.dominant_bark_type.value}},
        }
        properties["Source"] = {"select": {"name": episode.source.value}}
        if episode.clip_path:
            properties["Notes"] = {"rich_text": [{"text": {"content": f"Local clip: {episode.clip_path}"}}]}

        self._client.pages.update(page_id=page_id, properties=properties)
        logger.info("Updated preliminary page with confirmed episode: %s", page_id)

    def mark_unconfirmed(self, page_id: str, camera_name: str | None = None):
        """Update a preliminary page to indicate no bark was confirmed."""
        tz = ZoneInfo(settings.timezone)
        self._client.pages.update(
            page_id=page_id,
            properties={
                "Bark Type": {"select": {"name": "Unconfirmed"}},
                "Notes": {"rich_text": [{"text": {"content": "Sound event — no bark confirmed by YAMNet"}}]},
            },
        )
        logger.info("Marked page as unconfirmed: %s", page_id)

    def log_nest_event(self, timestamp: datetime, event_type: str,
                       camera_name: str | None = None, nest_link: str | None = None,
                       snapshot_path: str | None = None) -> str:
        """Log a Nest-only event (Sound/Bark detected by Nest, not confirmed by YAMNet).
        Returns the created page ID."""
        tz = ZoneInfo(settings.timezone)
        start = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=ZoneInfo("UTC"))
        local_time = start.astimezone(tz).strftime("%I:%M %p")
        cam_prefix = f"[{camera_name}] " if camera_name else ""
        # Map Nest event type to readable name
        event_label = event_type.split(".")[-1] if "." in event_type else event_type
        title = f"{cam_prefix}Nest {event_label} - {local_time}"

        properties = {
            "Event": {"title": [{"text": {"content": title}}]},
            "Date/Time": {"date": {"start": start.isoformat()}},
            "Bark Type": {"select": {"name": "Unconfirmed"}},
            "Confidence": {"number": 0},
            "Source": {"select": {"name": DetectionSource.NEST.value}},
            "Reason": {"select": {"name": "Unknown"}},
            "Owner Home": {"checkbox": False},
            "Intervened": {"checkbox": False},
        }
        if camera_name:
            properties["Camera"] = {"select": {"name": camera_name}}
        if nest_link:
            properties["Nest Link"] = {"url": nest_link}
        if snapshot_path:
            properties["Notes"] = {"rich_text": [{"text": {"content": f"Snapshot: {snapshot_path}"}}]}

        page = self._client.pages.create(
            parent={"database_id": self._database_id},
            properties=properties,
        )
        page_id = page.get("id", "")
        logger.info("Logged Nest-only event to Notion: %s", page_id)
        return page_id

    def upgrade_to_both(self, page_id: str, episode: Episode):
        """Upgrade a Nest-only event to 'Both' when YAMNet also confirms it."""
        episode.source = DetectionSource.BOTH
        self.update_episode(page_id, episode)

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
            "Source": {"select": {"name": episode.source.value}},
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

    def update_bark_type(self, page_id: str, bark_type: str):
        """Update the Bark Type select on a Notion page."""
        self._client.pages.update(
            page_id=page_id,
            properties={"Bark Type": {"select": {"name": bark_type}}},
        )
        logger.info("Updated Bark Type to '%s' for page %s", bark_type, page_id)

    def add_comment(self, page_id: str, text: str):
        """Add a comment to a Notion page."""
        self._http.post(
            "/comments",
            json={
                "parent": {"page_id": page_id},
                "rich_text": [{"text": {"content": text}}],
            },
        )
        logger.info("Added comment to page %s: %s", page_id, text[:50])

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

    def get_episodes_for_range(self, start_date: str, end_date: str | None = None) -> list[dict]:
        """Query bark episodes for a date range.

        Args:
            start_date: ISO date string (YYYY-MM-DD) for range start.
            end_date: Optional ISO date string for range end (exclusive next day).
                      If None, queries from start_date onwards.
        """
        if end_date:
            filter = {
                "and": [
                    {"property": "Date/Time", "date": {"on_or_after": start_date}},
                    {"property": "Date/Time", "date": {"before": end_date}},
                ]
            }
        else:
            filter = {
                "property": "Date/Time",
                "date": {"on_or_after": start_date},
            }

        result = self._query_database(
            filter=filter,
            sorts=[{"property": "Date/Time", "direction": "ascending"}],
        )
        return self._parse_episodes(result)

    def get_today_episodes(self) -> list[dict]:
        """Query today's bark episodes from Notion for the nightly summary."""
        tz = ZoneInfo(settings.timezone)
        today = datetime.now(tz).strftime("%Y-%m-%d")
        tomorrow = (datetime.now(tz) + timedelta(days=1)).strftime("%Y-%m-%d")
        return self.get_episodes_for_range(today, tomorrow)

    def _parse_episodes(self, result: list[dict]) -> list[dict]:

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
            source_sel = props.get("Source", {}).get("select")
            source = source_sel["name"] if source_sel else "YAMNet"

            episodes.append({
                "title": title,
                "start_time": start,
                "duration_seconds": duration,
                "bark_time_seconds": bark_time,
                "bark_count": bark_count,
                "bark_type": bark_type,
                "camera": camera,
                "source": source,
            })

        return episodes
