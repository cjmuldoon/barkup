"""Google Cloud Pub/Sub listener for Nest camera events."""

import json
import logging
from collections.abc import Callable
from datetime import datetime

from google.cloud import pubsub_v1

from barkup.config import settings

logger = logging.getLogger(__name__)

# Event types that should trigger audio analysis
TRIGGER_EVENT_TYPES = [
    "sdm.devices.events.CameraSound.Sound",
    "sdm.devices.events.CameraMotion.Motion",
    "sdm.devices.events.CameraPerson.Person",
]


class PubSubListener:
    def __init__(self, on_camera_event: Callable[[str, datetime, str, str], None]):
        """
        Args:
            on_camera_event: Callback with (event_id, event_timestamp, event_type, device_id).
        """
        self._on_camera_event = on_camera_event
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
            event_id, timestamp, event_type, device_id = self._extract_event(data)
            if event_id:
                logger.info("Camera event [%s]: %s at %s", event_type, event_id, timestamp)
                self._on_camera_event(event_id, timestamp, event_type, device_id)
            else:
                # Log non-trigger events for debugging
                event_types = list(data.get("resourceUpdate", {}).get("events", {}).keys())
                if event_types:
                    logger.info("Ignored event types: %s", event_types)
                else:
                    logger.info("Pub/Sub message with no matching events: %s", list(data.keys()))
        except Exception:
            logger.exception("Error processing Pub/Sub message")
        finally:
            message.ack()

    def _extract_event(
        self, data: dict
    ) -> tuple[str | None, datetime | None, str | None, str | None]:
        """Extract event ID, timestamp, type, and device ID from any trigger event."""
        resource_update = data.get("resourceUpdate", {})

        # Only process events for configured cameras
        device_id = resource_update.get("name", "")
        allowed_ids = settings.get_camera_ids()
        if allowed_ids and device_id not in allowed_ids:
            return None, None, None, None

        events = resource_update.get("events", {})

        # Check each trigger event type
        for event_type in TRIGGER_EVENT_TYPES:
            event_data = events.get(event_type)
            if event_data:
                event_id = event_data.get("eventId")
                timestamp_str = data.get("timestamp")
                timestamp = (
                    datetime.fromisoformat(timestamp_str)
                    if timestamp_str
                    else datetime.now()
                )
                return event_id, timestamp, event_type, device_id

        return None, None, None, None

    def start(self):
        """Start listening for events (blocking)."""
        logger.info("Listening for events on %s", self._subscription_path)
        logger.info("Trigger event types: %s", [t.split('.')[-1] for t in TRIGGER_EVENT_TYPES])
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
