"""Fetch event snapshot images from Nest cam."""

import logging
from pathlib import Path

import httpx

from barkup.config import settings
from barkup.google_auth import get_access_token
from barkup.sdm_client import SDMClient

logger = logging.getLogger(__name__)


def fetch_snapshot(sdm_client: SDMClient, event_id: str, save_dir: str | None = None) -> str | None:
    """
    Fetch a snapshot image for a camera event.
    Must be called within 30 seconds of the event.

    Returns the local file path if saved, or None on failure.
    """
    try:
        result = sdm_client.generate_image(event_id)
        image_url = result.get("url")
        token = result.get("token")
        if not image_url:
            logger.warning("No image URL in response")
            return None

        # Download the image
        resp = httpx.get(
            image_url,
            headers={"Authorization": f"Basic {token}"},
            timeout=15,
        )
        resp.raise_for_status()

        # Save locally
        save_path = Path(save_dir or settings.clip_storage_path)
        save_path.mkdir(parents=True, exist_ok=True)
        filename = f"snapshot_{event_id[:16]}.jpg"
        filepath = save_path / filename
        filepath.write_bytes(resp.content)
        logger.info("Snapshot saved: %s", filepath)
        return str(filepath)

    except Exception:
        logger.exception("Failed to fetch snapshot for event %s", event_id)
        return None
