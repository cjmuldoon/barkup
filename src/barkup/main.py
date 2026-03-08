"""Barkup - Main orchestrator.

Listens for Nest Cam sound events, runs bark classification,
groups detections into episodes, and logs to Notion.
"""

import argparse
import logging
import sys
import threading
import time
from datetime import datetime, time as dt_time
from pathlib import Path

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
        self._processing = False
        self._lock = threading.Lock()
        self._last_event_time: datetime | None = None
        self._event_cooldown = 10  # Ignore events within 10s of last one

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

    def _on_camera_event(self, event_id: str, timestamp: datetime, event_type: str, device_id: str):
        """Called by PubSub listener when a camera event arrives."""
        camera_name = settings.get_camera_name(device_id)
        logger.info("Camera event received [%s] from %s: %s", event_type, camera_name, event_id)

        # Cooldown: skip if we just processed an event
        now = datetime.now()
        if self._last_event_time:
            elapsed = (now - self._last_event_time).total_seconds()
            if elapsed < self._event_cooldown:
                logger.info("Skipping event (%.0fs since last, cooldown=%ds)", elapsed, self._event_cooldown)
                return
        self._last_event_time = now

        # Fetch snapshot immediately (30s expiry)
        from barkup.snapshot import fetch_snapshot
        snapshot_path = fetch_snapshot(self._sdm, device_id, event_id)

        with self._lock:
            if self._processing:
                logger.info("Already processing audio, ignoring duplicate event")
                return
            self._processing = True

        # Process in a thread to not block the Pub/Sub callback
        thread = threading.Thread(
            target=self._process_sound_event,
            args=(event_id, timestamp, snapshot_path, device_id),
            daemon=True,
        )
        thread.start()

    def _process_sound_event(
        self, event_id: str, timestamp: datetime, snapshot_path: str | None, device_id: str
    ):
        """Start RTSP stream, classify audio, track episodes."""
        from barkup.episode_tracker import EpisodeTracker
        from barkup.rtsp_stream import RTSPStream

        camera_name = settings.get_camera_name(device_id)
        stream = RTSPStream(self._sdm, device_id)
        tracker = EpisodeTracker()

        try:
            stream.start()

            # Start recording clip
            clip_dir = Path(settings.clip_storage_path)
            clip_dir.mkdir(parents=True, exist_ok=True)
            clip_filename = f"bark_{timestamp.strftime('%Y%m%d_%H%M%S')}.aac"
            clip_path = str(clip_dir / clip_filename)
            stream.start_recording(clip_path)

            # Build Nest app deep link
            device_parts = device_id.split("/")
            camera_id = device_parts[-1] if device_parts else ""
            nest_link = f"https://home.nest.com/camera/{camera_id}"

            consecutive_silence = 0
            max_silence_frames = int(
                settings.episode_cooldown_seconds / 0.96
            )

            while True:
                frame = stream.read_frame()
                if frame is None:
                    logger.warning("Audio stream ended unexpectedly")
                    break

                detection = self._classifier.classify_frame(frame)

                if detection.is_bark:
                    consecutive_silence = 0
                else:
                    consecutive_silence += 1

                episode = tracker.process(detection)
                if episode:
                    episode.snapshot_url = snapshot_path
                    episode.clip_path = clip_path
                    episode.nest_link = nest_link
                    episode.camera_name = camera_name
                    page_id = self._notion.log_episode(episode)
                    if self._telegram.enabled:
                        msg_id = self._telegram.send_bark_notification(episode, page_id)
                        if msg_id and page_id:
                            self._notion.set_telegram_message_id(page_id, msg_id)

                # Stop if silence exceeds cooldown and no active episode
                if (
                    consecutive_silence >= max_silence_frames
                    and not tracker.is_active
                ):
                    logger.info(
                        "Extended silence detected, stopping stream"
                    )
                    break

        except Exception:
            logger.exception("Error processing sound event")
        finally:
            # Finalize any in-progress episode
            remaining = tracker.force_end()
            if remaining:
                remaining.snapshot_url = snapshot_path
                remaining.clip_path = clip_path
                remaining.nest_link = nest_link if 'nest_link' in dir() else None
                remaining.camera_name = camera_name
                page_id = self._notion.log_episode(remaining)
                if self._telegram.enabled:
                    msg_id = self._telegram.send_bark_notification(remaining, page_id)
                    if msg_id and page_id:
                        self._notion.set_telegram_message_id(page_id, msg_id)

            stream.stop()
            with self._lock:
                self._processing = False

    def run(self):
        """Main entry point - start listening for events."""
        logger.info("Barkup starting...")
        camera_ids = settings.get_camera_ids()
        if camera_ids:
            for cid in camera_ids:
                logger.info("Camera: %s (%s)", settings.get_camera_name(cid), cid[-12:])
        else:
            logger.info("Cameras: all linked devices")
        logger.info("Notion DB: %s", settings.notion_database_id)

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
            logger.info("Nightly summary scheduled for %s:%02d", settings.summary_hour, settings.summary_minute)
        else:
            logger.info("Telegram not configured, notifications disabled")

        from barkup.pubsub_listener import PubSubListener
        listener = PubSubListener(on_camera_event=self._on_camera_event)
        listener.start()


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
