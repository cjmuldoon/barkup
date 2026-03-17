"""One-time migration script: Notion database -> SQLite.

Usage:
    python -m barkup.migrate_notion [--db-path ./data/barkup.db]

Pulls all episodes from the Notion database and inserts them into SQLite.
Safe to run multiple times — skips rows that already exist (matched by start_time + title).
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from barkup.config import settings
from barkup.db import BarkDatabase
from barkup.notion_logger import NotionLogger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def fetch_all_notion_episodes(notion: NotionLogger) -> list[dict]:
    """Fetch all episodes from Notion by paginating through date ranges."""
    tz = ZoneInfo(settings.timezone)
    all_episodes = []

    # Start from a year ago and work forward in monthly chunks
    start = datetime(2025, 1, 1, tzinfo=tz)
    end = datetime.now(tz) + timedelta(days=1)

    current = start
    while current < end:
        next_month = current + timedelta(days=31)
        if next_month > end:
            next_month = end

        start_str = current.strftime("%Y-%m-%d")
        end_str = next_month.strftime("%Y-%m-%d")

        logger.info("Fetching Notion episodes: %s to %s", start_str, end_str)
        try:
            episodes = notion.get_episodes_for_range(start_str, end_str)
            all_episodes.extend(episodes)
            logger.info("  Found %d episodes", len(episodes))
        except Exception:
            logger.exception("Failed to fetch range %s - %s", start_str, end_str)

        current = next_month

    return all_episodes


def migrate(db_path: str | None = None):
    """Run the Notion -> SQLite migration."""
    notion = NotionLogger()
    db = BarkDatabase(db_path)

    logger.info("Starting Notion -> SQLite migration")
    logger.info("Notion DB: %s", settings.notion_database_id)
    logger.info("SQLite DB: %s", db._db_path)

    episodes = fetch_all_notion_episodes(notion)
    logger.info("Total episodes from Notion: %d", len(episodes))

    inserted = 0
    skipped = 0

    for ep in episodes:
        # Check if already exists
        start_iso = ep["start_time"].isoformat() if ep["start_time"].tzinfo else ep["start_time"].isoformat()
        conn = db._get_conn()
        existing = conn.execute(
            "SELECT id FROM episodes WHERE event_title=? AND start_time LIKE ?",
            (ep["title"], start_iso[:19] + "%"),
        ).fetchone()

        if existing:
            skipped += 1
            continue

        # Insert
        conn.execute(
            """INSERT INTO episodes
               (event_title, start_time, duration_sec, bark_time_sec, bark_count,
                confidence, bark_type, camera, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ep["title"],
                start_iso,
                ep.get("duration_seconds", 0),
                ep.get("bark_time_seconds", 0),
                ep.get("bark_count", 0),
                ep.get("confidence", 0),
                ep.get("bark_type", "Unconfirmed"),
                ep.get("camera"),
                ep.get("source", "YAMNet"),
            ),
        )
        inserted += 1

    conn.commit()
    logger.info("Migration complete: %d inserted, %d skipped (already existed)", inserted, skipped)


def main():
    parser = argparse.ArgumentParser(description="Migrate Notion episodes to SQLite")
    parser.add_argument("--db-path", help="SQLite database path", default=None)
    args = parser.parse_args()
    migrate(args.db_path)


if __name__ == "__main__":
    main()
