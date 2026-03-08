"""Google Smart Device Management API client."""

import logging

import httpx

from barkup.config import settings
from barkup.google_auth import get_access_token

logger = logging.getLogger(__name__)

SDM_BASE = "https://smartdevicemanagement.googleapis.com/v1"


class SDMClient:
    def __init__(self):
        self._client = httpx.Client(timeout=30)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {get_access_token()}"}

    def list_devices(self) -> list[dict]:
        url = f"{SDM_BASE}/enterprises/{settings.sdm_project_id}/devices"
        resp = self._client.get(url, headers=self._headers())
        resp.raise_for_status()
        return resp.json().get("devices", [])

    def generate_rtsp_stream(self) -> dict:
        """Request an RTSP stream URL. Returns {streamUrls, streamToken, expiresAt}."""
        url = f"{SDM_BASE}/{settings.camera_device_id}:executeCommand"
        body = {
            "command": "sdm.devices.commands.CameraLiveStream.GenerateRtspStream",
            "params": {},
        }
        resp = self._client.post(url, headers=self._headers(), json=body)
        resp.raise_for_status()
        return resp.json().get("results", {})

    def extend_rtsp_stream(self, stream_token: str) -> dict:
        """Extend an active RTSP stream. Returns new {streamToken, expiresAt}."""
        url = f"{SDM_BASE}/{settings.camera_device_id}:executeCommand"
        body = {
            "command": "sdm.devices.commands.CameraLiveStream.ExtendRtspStream",
            "params": {"streamExtensionToken": stream_token},
        }
        resp = self._client.post(url, headers=self._headers(), json=body)
        resp.raise_for_status()
        return resp.json().get("results", {})

    def stop_rtsp_stream(self, stream_token: str) -> None:
        """Stop an active RTSP stream."""
        url = f"{SDM_BASE}/{settings.camera_device_id}:executeCommand"
        body = {
            "command": "sdm.devices.commands.CameraLiveStream.StopRtspStream",
            "params": {"streamExtensionToken": stream_token},
        }
        resp = self._client.post(url, headers=self._headers(), json=body)
        resp.raise_for_status()

    def generate_image(self, event_id: str) -> dict:
        """Get a snapshot image for an event. Returns {url, token}."""
        url = f"{SDM_BASE}/{settings.camera_device_id}:executeCommand"
        body = {
            "command": "sdm.devices.commands.CameraEventImage.GenerateImage",
            "params": {"eventId": event_id},
        }
        resp = self._client.post(url, headers=self._headers(), json=body)
        resp.raise_for_status()
        return resp.json().get("results", {})
