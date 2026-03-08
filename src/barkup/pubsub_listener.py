"""Google Cloud Pub/Sub listener for Nest camera events."""

import json
import logging
from collections.abc import Callable
from datetime import datetime

from google.cloud import pubsub_v1

from barkup.config import settings
from barkup.google_auth import get_credentials

logger = logging.getLogger(__name__)

SOUND_EVENT_TYPE = "sdm.devices.events.CameraSound.Sound"


class PubSubListener:
    def __init__(self, on_sound_event: Callable[[str, datetime], None]):
        """
        Args:
            on_sound_event: Callback with (event_id, event_timestamp) when sound detected.
        """
        self._on_sound_event = on_sound_event
        # Pub/Sub uses a GCP service account, not the OAuth user credentials
        if settings.google_application_credentials:
            self._subscriber = pubsub_v1.SubscriberClient.from_service_account_json(
                settings.google_application_credentials
            )
        else:
            self._subscriber = pubsub_v1.SubscriberClient()
        self._subscription_path = self._subscriber.subscription_path(
            settings.pubsub_project_id, settings.pubsub_subscription_id
        )
        self._streaming_pull_future = None

    def _handle_message(self, message: pubsub_v1.subscriber.message.Message):
        """Process a single Pub/Sub message."""
        try:
            data = json.loads(message.data.decode("utf-8"))
            # Log all incoming events for debugging
            event_types = list(data.get("resourceUpdate", {}).get("events", {}).keys())
            logger.info("Pub/Sub event received: %s", event_types or data.get("resourceUpdate", {}).get("traits", {}).keys() or "unknown")
            event_id, timestamp = self._extract_sound_event(data)
            if event_id:
                logger.info("Sound event: %s at %s", event_id, timestamp)
                self._on_sound_event(event_id, timestamp)
        except Exception:
            logger.exception("Error processing Pub/Sub message")
        finally:
            message.ack()

    def _extract_sound_event(
        self, data: dict
    ) -> tuple[str | None, datetime | None]:
        """Extract sound event ID and timestamp from SDM event payload."""
        # SDM event structure: resourceUpdate.events."sdm.devices.events.CameraSound.Sound"
        resource_update = data.get("resourceUpdate", {})

        # Only process events for our camera
        device_id = resource_update.get("name", "")
        if settings.camera_device_id and device_id != settings.camera_device_id:
            return None, None

        events = resource_update.get("events", {})
        sound_event = events.get(SOUND_EVENT_TYPE)
        if not sound_event:
            return None, None

        event_id = sound_event.get("eventId")
        timestamp_str = data.get("timestamp")
        timestamp = (
            datetime.fromisoformat(timestamp_str)
            if timestamp_str
            else datetime.now()
        )
        return event_id, timestamp

    def start(self):
        """Start listening for events (blocking)."""
        logger.info("Listening for events on %s", self._subscription_path)
        self._streaming_pull_future = self._subscriber.subscribe(
            self._subscription_path, callback=self._handle_message
        )
        try:
            self._streaming_pull_future.result()
        except KeyboardInterrupt:
            self._streaming_pull_future.cancel()
            self._streaming_pull_future.result()

    def stop(self):
        if self._streaming_pull_future:
            self._streaming_pull_future.cancel()
