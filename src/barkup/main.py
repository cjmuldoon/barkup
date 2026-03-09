"""Barkup - Main orchestrator.

Always-on RTSP monitoring with YAMNet bark classification.
Nest Pub/Sub events provide snapshots and cross-referencing.
"""

import argparse
import logging
import signal
import threading
import time
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from barkup.config import settings
from barkup.sdm_client import SDMClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class BarkupOrchestrator:
    def __init__(self):
        from barkup.bark_classifier import BarkClassifier
        from barkup.notion_logger import NotionLogger
        from barkup.telegram_bot import TelegramBot

        self._sdm = SDMClient()
        self._classifier = BarkClassifier()
        self._notion = NotionLogger()
        self._telegram = TelegramBot(
            on_intervention=self._handle_intervention,
            notion_logger=self._notion,
        )
        self._tz = ZoneInfo(settings.timezone)
        self._shutdown = threading.Event()
        self._monitor_active = threading.Event()

        # Nest event cross-referencing: recent Nest Sound events keyed by device_id
        # Each entry: (timestamp, event_type, snapshot_path, nest_link, page_id)
        self._nest_events: dict[str, list[dict]] = {}
        self._nest_lock = threading.Lock()
        self._nest_event_window = 60  # seconds to match Nest event to YAMNet episode

    def _handle_intervention(self, page_id: str, fields: dict):
        """Handle intervention reply from Telegram."""
        try:
            self._notion.update_intervention(page_id, fields)
            logger.info("Updated intervention for page %s: %s", page_id, fields)
        except Exception:
            logger.exception("Failed to update intervention")

    def _send_nightly_summary(self):
        """Send nightly summary via Telegram, querying Notion for today's data."""
        episodes = self._notion.get_today_episodes()
        self._telegram.send_nightly_summary(episodes)
        logger.info("Nightly summary sent: %d episodes", len(episodes))

    # --- Nest event handling (snapshots + cross-referencing) ---

    def _on_camera_event(self, event_id: str, timestamp: datetime, event_type: str, device_id: str):
        """Called by PubSub listener when a camera event arrives.

        In always-on mode, this captures snapshots and logs Nest events
        for cross-referencing with YAMNet detections.
        """
        camera_name = settings.get_camera_name(device_id)
        event_label = event_type.split(".")[-1] if "." in event_type else event_type
        logger.info("Nest event [%s] from %s at %s", event_label, camera_name, timestamp)

        # Fetch snapshot (30s expiry)
        from barkup.snapshot import fetch_snapshot
        snapshot_path = fetch_snapshot(self._sdm, device_id, event_id)

        # Build Nest app deep link
        device_parts = device_id.split("/")
        camera_id_part = device_parts[-1] if device_parts else ""
        nest_link = f"https://home.nest.com/camera/{camera_id_part}"

        # Only track Sound events for cross-referencing
        if "Sound" not in event_type:
            return

        # Store for cross-referencing with YAMNet
        nest_event = {
            "timestamp": timestamp,
            "event_type": event_type,
            "snapshot_path": snapshot_path,
            "nest_link": nest_link,
            "camera_name": camera_name,
            "matched": False,  # Set True when YAMNet also detects bark
            "page_id": None,   # Filled if we create a Nest-only page
        }

        with self._nest_lock:
            if device_id not in self._nest_events:
                self._nest_events[device_id] = []
            self._nest_events[device_id].append(nest_event)

        # If monitoring is NOT active, log as Nest-only immediately
        # (barking outside monitoring hours)
        if not self._monitor_active.is_set():
            try:
                page_id = self._notion.log_nest_event(
                    timestamp=timestamp, event_type=event_type,
                    camera_name=camera_name, nest_link=nest_link,
                    snapshot_path=snapshot_path,
                )
                nest_event["page_id"] = page_id
                if self._telegram.enabled:
                    msg_id = self._telegram.send_preliminary_notification(
                        timestamp=timestamp, camera_name=camera_name, nest_link=nest_link,
                    )
                    if msg_id and page_id:
                        self._notion.set_telegram_message_id(page_id, msg_id)
                logger.info("Nest-only event logged (outside monitoring hours): %s", page_id)
            except Exception:
                logger.exception("Failed to log Nest-only event")

    def _find_matching_nest_event(self, device_id: str, episode_start: datetime) -> dict | None:
        """Find a recent unmatched Nest Sound event near an episode start time."""
        with self._nest_lock:
            events = self._nest_events.get(device_id, [])
            for event in reversed(events):  # Check most recent first
                if event["matched"]:
                    continue
                nest_ts = event["timestamp"]
                # Normalize both to naive for comparison
                ts_a = nest_ts.replace(tzinfo=None) if nest_ts.tzinfo else nest_ts
                ts_b = episode_start.replace(tzinfo=None) if episode_start.tzinfo else episode_start
                diff = abs((ts_b - ts_a).total_seconds())
                if diff <= self._nest_event_window:
                    event["matched"] = True
                    return event
        return None

    def _cleanup_old_nest_events(self, device_id: str):
        """Remove Nest events older than the matching window."""
        cutoff = datetime.now() - timedelta(seconds=self._nest_event_window * 2)
        with self._nest_lock:
            events = self._nest_events.get(device_id, [])
            # Log unmatched events as Nest-only before removing
            remaining = []
            for event in events:
                event_age = datetime.now()
                nest_ts = event["timestamp"]
                if nest_ts.tzinfo:
                    nest_ts = nest_ts.replace(tzinfo=None)
                if (event_age - nest_ts).total_seconds() > self._nest_event_window * 2:
                    if not event["matched"] and not event.get("page_id") and self._monitor_active.is_set():
                        # Nest detected sound but YAMNet didn't — log as Nest-only
                        try:
                            page_id = self._notion.log_nest_event(
                                timestamp=event["timestamp"],
                                event_type=event["event_type"],
                                camera_name=event["camera_name"],
                                nest_link=event["nest_link"],
                                snapshot_path=event["snapshot_path"],
                            )
                            logger.info("Nest-only event logged (YAMNet didn't confirm): %s", page_id)
                        except Exception:
                            logger.exception("Failed to log Nest-only event")
                else:
                    remaining.append(event)
            self._nest_events[device_id] = remaining

    # --- Always-on classification loop ---

    def _run_classification_loop(self, device_id: str):
        """Continuously classify audio from an always-on RTSP stream."""
        from barkup.episode_tracker import EpisodeTracker
        from barkup.models import DetectionSource
        from barkup.rtsp_stream import RTSPStream

        camera_name = settings.get_camera_name(device_id)
        device_parts = device_id.split("/")
        camera_id = device_parts[-1] if device_parts else ""
        nest_link = f"https://home.nest.com/camera/{camera_id}"

        reconnect_delay = settings.stream_reconnect_delay

        while self._monitor_active.is_set() and not self._shutdown.is_set():
            stream = RTSPStream(self._sdm, device_id)
            tracker = EpisodeTracker()
            clip_path = None
            recording = False

            try:
                logger.info("Starting RTSP stream for %s", camera_name)
                stream.start()
                reconnect_delay = settings.stream_reconnect_delay  # Reset on success

                while self._monitor_active.is_set() and not self._shutdown.is_set():
                    frame = stream.read_frame()
                    if frame is None:
                        logger.warning("RTSP stream ended for %s, will reconnect", camera_name)
                        break

                    detection = self._classifier.classify_frame(frame)
                    was_active = tracker.is_active

                    episode = tracker.process(detection)

                    # Start recording when episode begins
                    if tracker.is_active and not was_active and not recording:
                        clip_dir = Path(settings.clip_storage_path)
                        clip_dir.mkdir(parents=True, exist_ok=True)
                        clip_filename = f"bark_{datetime.now().strftime('%Y%m%d_%H%M%S')}.aac"
                        clip_path = str(clip_dir / clip_filename)
                        stream.start_recording(clip_path)
                        recording = True

                    if episode:
                        # Stop recording
                        if recording:
                            stream.stop_recording()
                            recording = False

                        episode.camera_name = camera_name
                        episode.nest_link = nest_link
                        episode.clip_path = clip_path
                        clip_path = None

                        # Cross-reference with Nest events
                        nest_event = self._find_matching_nest_event(device_id, episode.start_time)
                        if nest_event:
                            episode.source = DetectionSource.BOTH
                            episode.snapshot_url = nest_event.get("snapshot_path")
                            if nest_event.get("page_id"):
                                # Upgrade existing Nest-only page
                                self._notion.upgrade_to_both(nest_event["page_id"], episode)
                                logger.info("Upgraded Nest event to Both: %s", nest_event["page_id"])
                                continue
                        else:
                            episode.source = DetectionSource.YAMNET

                        # Log to Notion + Telegram
                        page_id = self._notion.log_episode(episode)
                        if self._telegram.enabled:
                            msg_id = self._telegram.send_bark_notification(episode, page_id)
                            if msg_id and page_id:
                                self._notion.set_telegram_message_id(page_id, msg_id)

                    # If episode just ended, stop recording if still going
                    if was_active and not tracker.is_active and recording:
                        stream.stop_recording()
                        recording = False

                    # Periodically clean up old Nest events
                    if self._classifier._frame_count % 120 == 0:
                        self._cleanup_old_nest_events(device_id)

            except Exception:
                logger.exception("Error in classification loop for %s", camera_name)
            finally:
                # Finalize any in-progress episode
                remaining = tracker.force_end()
                if remaining:
                    if recording:
                        stream.stop_recording()
                        recording = False
                    remaining.camera_name = camera_name
                    remaining.nest_link = nest_link
                    remaining.clip_path = clip_path

                    nest_event = self._find_matching_nest_event(device_id, remaining.start_time)
                    if nest_event:
                        remaining.source = DetectionSource.BOTH
                        remaining.snapshot_url = nest_event.get("snapshot_path")

                    page_id = self._notion.log_episode(remaining)
                    if self._telegram.enabled:
                        msg_id = self._telegram.send_bark_notification(remaining, page_id)
                        if msg_id and page_id:
                            self._notion.set_telegram_message_id(page_id, msg_id)

                # Don't release server-side stream if we're about to reconnect
                will_reconnect = self._monitor_active.is_set() and not self._shutdown.is_set()
                stream.stop(release_stream=not will_reconnect)

            # Reconnect if still within monitoring window
            if self._monitor_active.is_set() and not self._shutdown.is_set():
                logger.info("Reconnecting in %ds...", reconnect_delay)
                self._shutdown.wait(timeout=reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)  # Exponential backoff, max 60s

        logger.info("Classification loop ended for %s", camera_name)

    # --- Monitor schedule ---

    def _run_monitor_schedule(self):
        """Start/stop RTSP monitoring based on configured hours."""
        while not self._shutdown.is_set():
            now = datetime.now(self._tz)
            start_time = now.replace(
                hour=settings.monitor_start_hour,
                minute=settings.monitor_start_minute,
                second=0, microsecond=0,
            )
            end_time = now.replace(
                hour=settings.monitor_end_hour,
                minute=settings.monitor_end_minute,
                second=0, microsecond=0,
            )

            if start_time <= now < end_time:
                # Within monitoring window
                wait_seconds = (end_time - now).total_seconds()
                logger.info(
                    "Monitoring window active until %s (%s) — %.0f minutes remaining",
                    end_time.strftime("%I:%M %p"), settings.timezone,
                    wait_seconds / 60,
                )
                self._start_monitoring()
                self._shutdown.wait(timeout=wait_seconds)
                self._stop_monitoring()
            else:
                # Outside window — wait until next start
                if now >= end_time:
                    next_start = start_time + timedelta(days=1)
                else:
                    next_start = start_time
                wait_seconds = (next_start - now).total_seconds()
                logger.info(
                    "Outside monitoring window. Next start: %s (%s) — %.0f minutes",
                    next_start.strftime("%I:%M %p"), settings.timezone,
                    wait_seconds / 60,
                )
                self._shutdown.wait(timeout=wait_seconds)

    def _start_monitoring(self):
        """Start classification loops for all configured cameras."""
        if self._monitor_active.is_set():
            return

        self._monitor_active.set()
        camera_ids = settings.get_camera_ids()

        if not camera_ids:
            # "all" mode: discover cameras
            try:
                devices = self._sdm.list_devices()
                camera_ids = [
                    d["name"] for d in devices
                    if "sdm.devices.traits.CameraLiveStream" in d.get("traits", {})
                ]
            except Exception:
                logger.exception("Failed to discover cameras")
                return

        for device_id in camera_ids:
            camera_name = settings.get_camera_name(device_id)
            logger.info("Starting always-on monitoring for %s", camera_name)
            thread = threading.Thread(
                target=self._run_classification_loop,
                args=(device_id,),
                daemon=True,
                name=f"monitor-{camera_name}",
            )
            thread.start()

    def _stop_monitoring(self):
        """Signal classification loops to stop."""
        logger.info("Stopping monitoring (end of window)")
        self._monitor_active.clear()
        # Classification loops will exit on their next iteration

    # --- Main entry point ---

    def run(self):
        """Main entry point."""
        logger.info("Barkup starting (always-on mode)...")
        logger.info(
            "Monitor window: %02d:%02d – %02d:%02d %s",
            settings.monitor_start_hour, settings.monitor_start_minute,
            settings.monitor_end_hour, settings.monitor_end_minute,
            settings.timezone,
        )
        camera_ids = settings.get_camera_ids()
        if camera_ids:
            for cid in camera_ids:
                logger.info("Camera: %s (%s)", settings.get_camera_name(cid), cid[-12:])
        else:
            logger.info("Cameras: all linked devices")
        logger.info("Notion DB: %s", settings.notion_database_id)

        # Graceful shutdown
        def shutdown_handler(signum, frame):
            logger.info("Shutdown signal received")
            self._shutdown.set()
            self._monitor_active.clear()

        signal.signal(signal.SIGTERM, shutdown_handler)
        signal.signal(signal.SIGINT, shutdown_handler)

        # Start Telegram bot polling for replies
        if self._telegram.enabled:
            self._telegram.start_polling()
            logger.info("Telegram notifications enabled")

            # Start nightly summary scheduler
            from barkup.scheduler import DailyScheduler
            summary_time = dt_time(settings.summary_hour, settings.summary_minute)
            self._scheduler = DailyScheduler(
                target_time=summary_time,
                callback=self._send_nightly_summary,
            )
            self._scheduler.start()
            logger.info("Nightly summary scheduled for %02d:%02d", settings.summary_hour, settings.summary_minute)
        else:
            logger.info("Telegram not configured, notifications disabled")

        # Start Pub/Sub listener in background (for snapshots + cross-referencing)
        from barkup.pubsub_listener import PubSubListener
        listener = PubSubListener(on_camera_event=self._on_camera_event)
        pubsub_thread = threading.Thread(target=listener.start, daemon=True, name="pubsub")
        pubsub_thread.start()
        logger.info("Pub/Sub listener started (snapshots + cross-referencing)")

        # Run monitor schedule (blocks until shutdown)
        self._run_monitor_schedule()

        logger.info("Barkup shutting down")


def list_devices():
    """Print available devices."""
    sdm = SDMClient()
    devices = sdm.list_devices()
    if not devices:
        print("No devices found. Check your SDM project and OAuth setup.")
        return
    for device in devices:
        name = device.get("name", "unknown")
        device_type = device.get("type", "unknown")
        traits = list(device.get("traits", {}).keys())
        print(f"\nDevice: {name}")
        print(f"  Type: {device_type}")
        print(f"  Traits: {', '.join(traits)}")


def main():
    parser = argparse.ArgumentParser(description="Barkup - Dog Bark Tracker")
    parser.add_argument(
        "--list-devices", action="store_true", help="List available Nest devices"
    )
    args = parser.parse_args()

    if args.list_devices:
        list_devices()
    else:
        orchestrator = BarkupOrchestrator()
        orchestrator.run()


if __name__ == "__main__":
    main()
