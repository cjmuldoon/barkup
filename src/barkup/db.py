"""SQLite database layer for bark episode storage."""

import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from barkup.config import settings
from barkup.models import DetectionSource, Episode

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_title     TEXT NOT NULL,
    start_time      TEXT NOT NULL,
    end_time        TEXT,
    duration_sec    REAL,
    bark_time_sec   REAL,
    bark_count      INTEGER,
    confidence      REAL,
    bark_type       TEXT DEFAULT 'Unconfirmed',
    reason          TEXT DEFAULT 'Unknown',
    camera          TEXT,
    source          TEXT DEFAULT 'YAMNet',
    owner_home      INTEGER DEFAULT 0,
    intervened      INTEGER DEFAULT 0,
    nest_link       TEXT,
    clip_path       TEXT,
    video_path      TEXT,
    snapshot_path   TEXT,
    notes           TEXT,
    telegram_msg_id INTEGER,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_episodes_start ON episodes(start_time);
CREATE INDEX IF NOT EXISTS idx_episodes_bark_type ON episodes(bark_type);
CREATE INDEX IF NOT EXISTS idx_episodes_telegram ON episodes(telegram_msg_id);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now'))
);
"""


class BarkDatabase:
    """Thread-safe SQLite database for bark episodes."""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or settings.db_path
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self._db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
        return self._local.conn

    def _init_schema(self):
        conn = self._get_conn()
        conn.executescript(SCHEMA)
        conn.commit()

    # --- Episode CRUD ---

    def _build_title(self, episode: Episode) -> str:
        tz = ZoneInfo(settings.timezone)
        start = episode.start_time if episode.start_time.tzinfo else episode.start_time.replace(tzinfo=ZoneInfo("UTC"))
        local_time = start.astimezone(tz).strftime("%I:%M %p")
        duration_min = episode.duration_seconds / 60
        cam_prefix = f"[{episode.camera_name}] " if episode.camera_name else ""
        if duration_min >= 1:
            return f"{cam_prefix}{episode.dominant_bark_type.value} - {local_time} ({duration_min:.0f}m)"
        return f"{cam_prefix}{episode.dominant_bark_type.value} - {local_time} ({episode.duration_seconds:.0f}s)"

    def _ensure_iso(self, dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.isoformat()

    def log_episode(self, episode: Episode) -> int:
        """Insert a completed episode. Returns row ID."""
        title = self._build_title(episode)
        conn = self._get_conn()
        cur = conn.execute(
            """INSERT INTO episodes
               (event_title, start_time, end_time, duration_sec, bark_time_sec,
                bark_count, confidence, bark_type, reason, camera, source,
                owner_home, intervened, nest_link, clip_path, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Unknown', ?, ?, 0, 0, ?, ?, ?)""",
            (
                title,
                self._ensure_iso(episode.start_time),
                self._ensure_iso(episode.end_time),
                episode.duration_seconds,
                round(episode.bark_frame_count * 0.975, 1),
                episode.bark_frame_count,
                episode.peak_confidence,
                episode.dominant_bark_type.value,
                episode.camera_name,
                episode.source.value,
                episode.nest_link,
                episode.clip_path,
                f"Local clip: {episode.clip_path}" if episode.clip_path else None,
            ),
        )
        conn.commit()
        row_id = cur.lastrowid
        logger.info("Logged episode to DB: id=%d", row_id)
        return row_id

    def log_preliminary(self, timestamp: datetime, camera_name: str | None = None,
                        snapshot_path: str | None = None, nest_link: str | None = None) -> int:
        """Create a preliminary row for a Sound event. Returns row ID."""
        tz = ZoneInfo(settings.timezone)
        start = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=ZoneInfo("UTC"))
        local_time = start.astimezone(tz).strftime("%I:%M %p")
        cam_prefix = f"[{camera_name}] " if camera_name else ""
        title = f"{cam_prefix}Sound detected - {local_time} (analyzing...)"

        conn = self._get_conn()
        cur = conn.execute(
            """INSERT INTO episodes
               (event_title, start_time, bark_type, confidence, reason, camera, nest_link, snapshot_path, notes)
               VALUES (?, ?, 'Unconfirmed', 0, 'Unknown', ?, ?, ?, ?)""",
            (
                title,
                self._ensure_iso(start),
                camera_name,
                nest_link,
                snapshot_path,
                f"Snapshot: {snapshot_path}" if snapshot_path else None,
            ),
        )
        conn.commit()
        row_id = cur.lastrowid
        logger.info("Preliminary entry created: id=%d", row_id)
        return row_id

    def update_episode(self, row_id: int, episode: Episode):
        """Update a preliminary row with confirmed episode details."""
        title = self._build_title(episode)
        conn = self._get_conn()
        conn.execute(
            """UPDATE episodes SET
               event_title=?, start_time=?, end_time=?, duration_sec=?,
               bark_time_sec=?, bark_count=?, confidence=?, bark_type=?,
               source=?, clip_path=?, notes=?
               WHERE id=?""",
            (
                title,
                self._ensure_iso(episode.start_time),
                self._ensure_iso(episode.end_time),
                episode.duration_seconds,
                round(episode.bark_frame_count * 0.975, 1),
                episode.bark_frame_count,
                episode.peak_confidence,
                episode.dominant_bark_type.value,
                episode.source.value,
                episode.clip_path,
                f"Local clip: {episode.clip_path}" if episode.clip_path else None,
                row_id,
            ),
        )
        conn.commit()
        logger.info("Updated episode id=%d", row_id)

    def mark_unconfirmed(self, row_id: int):
        conn = self._get_conn()
        conn.execute(
            "UPDATE episodes SET bark_type='Unconfirmed', notes='Sound event — no bark confirmed by YAMNet' WHERE id=?",
            (row_id,),
        )
        conn.commit()

    def log_nest_event(self, timestamp: datetime, event_type: str,
                       camera_name: str | None = None, nest_link: str | None = None,
                       snapshot_path: str | None = None) -> int:
        """Log a Nest-only event. Returns row ID."""
        tz = ZoneInfo(settings.timezone)
        start = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=ZoneInfo("UTC"))
        local_time = start.astimezone(tz).strftime("%I:%M %p")
        cam_prefix = f"[{camera_name}] " if camera_name else ""
        event_label = event_type.split(".")[-1] if "." in event_type else event_type
        title = f"{cam_prefix}Nest {event_label} - {local_time}"

        conn = self._get_conn()
        cur = conn.execute(
            """INSERT INTO episodes
               (event_title, start_time, bark_type, confidence, source, reason,
                camera, nest_link, snapshot_path, notes)
               VALUES (?, ?, 'Unconfirmed', 0, ?, 'Unknown', ?, ?, ?, ?)""",
            (
                title,
                self._ensure_iso(start),
                DetectionSource.NEST.value,
                camera_name,
                nest_link,
                snapshot_path,
                f"Snapshot: {snapshot_path}" if snapshot_path else None,
            ),
        )
        conn.commit()
        row_id = cur.lastrowid
        logger.info("Logged Nest-only event to DB: id=%d", row_id)
        return row_id

    def upgrade_to_both(self, row_id: int, episode: Episode):
        episode.source = DetectionSource.BOTH
        self.update_episode(row_id, episode)

    def update_bark_type(self, row_id: int, bark_type: str):
        conn = self._get_conn()
        conn.execute("UPDATE episodes SET bark_type=? WHERE id=?", (bark_type, row_id))
        conn.commit()

    def update_intervention(self, row_id: int, fields: dict):
        conn = self._get_conn()
        updates = []
        params = []
        if "was_home" in fields:
            updates.append("owner_home=?")
            params.append(1 if fields["was_home"] else 0)
        if fields.get("intervened"):
            updates.append("intervened=1")
        if fields.get("reason"):
            reason_map = {
                "stranger": "Stranger", "delivery": "Delivery", "animal": "Animal",
                "boredom": "Boredom", "anxiety": "Anxiety", "doorbell": "Doorbell",
            }
            matched = reason_map.get(fields["reason"].lower(), "Other")
            updates.append("reason=?")
            params.append(matched)
            if matched == "Other":
                updates.append("notes=?")
                params.append(f"Reason: {fields['reason']}")
        if updates:
            params.append(row_id)
            conn.execute(f"UPDATE episodes SET {', '.join(updates)} WHERE id=?", params)
            conn.commit()

    def set_telegram_message_id(self, row_id: int, message_id: int):
        conn = self._get_conn()
        conn.execute("UPDATE episodes SET telegram_msg_id=? WHERE id=?", (message_id, row_id))
        conn.commit()

    def find_page_by_message_id(self, message_id: int) -> int | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT id FROM episodes WHERE telegram_msg_id=? LIMIT 1", (message_id,)
        ).fetchone()
        return row["id"] if row else None

    # --- Queries ---

    def get_episodes_for_range(self, start_date: str, end_date: str | None = None) -> list[dict]:
        """Query episodes by date range (local dates YYYY-MM-DD)."""
        tz = ZoneInfo(settings.timezone)
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=tz)
        start_iso = start_dt.isoformat()

        conn = self._get_conn()
        if end_date:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=tz)
            end_iso = end_dt.isoformat()
            rows = conn.execute(
                "SELECT * FROM episodes WHERE start_time >= ? AND start_time < ? ORDER BY start_time",
                (start_iso, end_iso),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM episodes WHERE start_time >= ? ORDER BY start_time",
                (start_iso,),
            ).fetchall()
        return self._parse_rows(rows)

    def get_today_episodes(self) -> list[dict]:
        tz = ZoneInfo(settings.timezone)
        today = datetime.now(tz).strftime("%Y-%m-%d")
        tomorrow = (datetime.now(tz) + timedelta(days=1)).strftime("%Y-%m-%d")
        return self.get_episodes_for_range(today, tomorrow)

    def get_recent_episodes(self, limit: int = 20) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM episodes ORDER BY start_time DESC LIMIT ?", (limit,)
        ).fetchall()
        return self._parse_rows(rows)

    def get_hourly_bark_minutes(self, date: str | None = None) -> dict[int, float]:
        """Return {hour: bark_minutes} for a given date (default today)."""
        tz = ZoneInfo(settings.timezone)
        if date is None:
            date = datetime.now(tz).strftime("%Y-%m-%d")
        tomorrow = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

        start_dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=tz)
        end_dt = datetime.strptime(tomorrow, "%Y-%m-%d").replace(tzinfo=tz)

        conn = self._get_conn()
        rows = conn.execute(
            """SELECT start_time, bark_time_sec FROM episodes
               WHERE start_time >= ? AND start_time < ?
               AND bark_type NOT IN ('Not Bark', 'Unconfirmed')""",
            (start_dt.isoformat(), end_dt.isoformat()),
        ).fetchall()

        hourly = {}
        for row in rows:
            dt = datetime.fromisoformat(row["start_time"]).astimezone(tz)
            hour = dt.hour
            bark_min = (row["bark_time_sec"] or 0) / 60
            hourly[hour] = hourly.get(hour, 0) + bark_min
        return hourly

    def get_daily_summary(self, date: str | None = None) -> dict:
        """Summary stats for a day."""
        tz = ZoneInfo(settings.timezone)
        if date is None:
            date = datetime.now(tz).strftime("%Y-%m-%d")
        tomorrow = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        episodes = self.get_episodes_for_range(date, tomorrow)

        confirmed = [e for e in episodes if e["bark_type"] not in ("Not Bark", "Unconfirmed")]
        total_bark_sec = sum(e["bark_time_seconds"] for e in confirmed)

        # Find peak hour
        hourly = self.get_hourly_bark_minutes(date)
        peak_hour = max(hourly, key=hourly.get) if hourly else None

        return {
            "date": date,
            "total_episodes": len(confirmed),
            "total_bark_minutes": round(total_bark_sec / 60, 1),
            "dismissed": len([e for e in episodes if e["bark_type"] == "Not Bark"]),
            "peak_hour": peak_hour,
            "hourly_bark_minutes": hourly,
            "episodes": confirmed,
        }

    def _parse_rows(self, rows: list) -> list[dict]:
        episodes = []
        for row in rows:
            start_str = row["start_time"]
            if not start_str:
                continue
            episodes.append({
                "id": row["id"],
                "title": row["event_title"],
                "start_time": datetime.fromisoformat(start_str),
                "duration_seconds": row["duration_sec"] or 0,
                "bark_time_seconds": row["bark_time_sec"] or 0,
                "bark_count": row["bark_count"] or 0,
                "confidence": row["confidence"] or 0,
                "bark_type": row["bark_type"] or "Unconfirmed",
                "reason": row["reason"] or "Unknown",
                "camera": row["camera"],
                "source": row["source"] or "YAMNet",
                "owner_home": bool(row["owner_home"]),
                "intervened": bool(row["intervened"]),
                "nest_link": row["nest_link"],
                "clip_path": row["clip_path"],
                "snapshot_path": row["snapshot_path"],
                "notes": row["notes"],
            })
        return episodes

    # --- User management ---

    def create_user(self, username: str, password_hash: str):
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, password_hash),
        )
        conn.commit()

    def get_user(self, username: str) -> dict | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM users WHERE username=?", (username,)
        ).fetchone()
        if row:
            return {"id": row["id"], "username": row["username"], "password_hash": row["password_hash"]}
        return None
