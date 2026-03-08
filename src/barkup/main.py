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
        self._telegram = TelegramBot(on_intervention=self._handle_intervention)
        self._processing = False
        self._lock = threading.Lock()
        # Track today's episodes for nightly summary
        self._today_episodes: list = []
        self._today_date = datetime.now().date()

    def _handle_intervention(self, page_id: str, fields: dict):
        """Handle intervention reply from Telegram."""
        try:
            self._notion.update_intervention(page_id, fields)
            logger.info("Updated intervention for page %s: %s", page_id, fields)
        except Exception:
            logger.exception("Failed to update intervention")

    def _track_episode(self, episode):
        """Add episode to today's list, resetting if new day."""
        today = datetime.now().date()
        if today != self._today_date:
            self._today_episodes = []
            self._today_date = today
        self._today_episodes.append(episode)

    def _send_nightly_summary(self):
        """Send nightly summary via Telegram."""
        today = datetime.now().date()
        if today != self._today_date:
            # Day rolled over, send yesterday's summary
            episodes = self._today_episodes
            self._today_episodes = []
            self._today_date = today
        else:
            episodes = self._today_episodes

        self._telegram.send_nightly_summary(episodes)
        logger.info("Nightly summary sent: %d episodes", len(episodes))

    def _on_sound_event(self, event_id: str, timestamp: datetime):
        """Called by PubSub listener when a sound event arrives."""
        logger.info("Sound event received: %s", event_id)

        # Fetch snapshot immediately (30s expiry)
        from barkup.snapshot import fetch_snapshot
        snapshot_path = fetch_snapshot(self._sdm, event_id)

        with self._lock:
            if self._processing:
                logger.info("Already processing audio, ignoring duplicate event")
                return
            self._processing = True

        # Process in a thread to not block the Pub/Sub callback
        thread = threading.Thread(
            target=self._process_sound_event,
            args=(event_id, timestamp, snapshot_path),
            daemon=True,
        )
        thread.start()

    def _process_sound_event(
        self, event_id: str, timestamp: datetime, snapshot_path: str | None
    ):
        """Start RTSP stream, classify audio, track episodes."""
        from barkup.episode_tracker import EpisodeTracker
        from barkup.rtsp_stream import RTSPStream

        stream = RTSPStream(self._sdm)
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
            device_parts = settings.camera_device_id.split("/")
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
                    page_id = self._notion.log_episode(episode)
                    self._track_episode(episode)
                    # Send Telegram notification
                    if self._telegram.enabled:
                        self._telegram.send_bark_notification(episode, page_id)

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
                page_id = self._notion.log_episode(remaining)
                self._track_episode(remaining)
                if self._telegram.enabled:
                    self._telegram.send_bark_notification(remaining, page_id)

            stream.stop()
            with self._lock:
                self._processing = False

    def run(self):
        """Main entry point - start listening for events."""
        logger.info("Barkup starting...")
        logger.info("Camera: %s", settings.camera_device_id)
        logger.info("Notion DB: %s", settings.notion_database_id)

        # Start Telegram bot polling for replies
        if self._telegram.enabled:
            self._telegram.start_polling()
            logger.info("Telegram notifications enabled")

            # Start nightly summary scheduler
            from barkup.scheduler import DailyScheduler
            summary_time = dt_time(settings.summary_hour, 0)
            self._scheduler = DailyScheduler(
                target_time=summary_time,
                callback=self._send_nightly_summary,
            )
            self._scheduler.start()
            logger.info("Nightly summary scheduled for %s:00", settings.summary_hour)
        else:
            logger.info("Telegram not configured, notifications disabled")

        from barkup.pubsub_listener import PubSubListener
        listener = PubSubListener(on_sound_event=self._on_sound_event)
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
