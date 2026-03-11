"""RTSP stream manager using ffmpeg for audio extraction."""

import logging
import os
import select
import subprocess
import threading
import time

from barkup.sdm_client import SDMClient

logger = logging.getLogger(__name__)

# ffmpeg outputs 16kHz mono 16-bit PCM
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit = 2 bytes
FRAME_SAMPLES = 15600  # YAMNet expected input size
FRAME_BYTES = FRAME_SAMPLES * SAMPLE_WIDTH

# Extend stream every 4.5 minutes (streams expire at 5 min)
EXTEND_INTERVAL = 270

# Full reconnect every 15 minutes to prevent RTSP relay data stalls
RECONNECT_INTERVAL = 900


class RTSPStream:
    def __init__(self, sdm_client: SDMClient, device_id: str):
        self._sdm = sdm_client
        self._device_id = device_id
        self._process: subprocess.Popen | None = None
        self._stream_token: str | None = None
        self._rtsp_url: str | None = None
        self._extend_timer: threading.Timer | None = None
        self._recording_process: subprocess.Popen | None = None
        self._video_process: subprocess.Popen | None = None
        self._active = False
        self._stream_started_at: float = 0
        self._consecutive_slow: int = 0  # Count of consecutive slow reads

    def start(self) -> None:
        """Start RTSP stream and ffmpeg audio extraction."""
        result = self._sdm.generate_rtsp_stream(self._device_id)
        self._rtsp_url = result["streamUrls"]["rtspUrl"]
        self._stream_token = result["streamExtensionToken"]
        self._active = True

        # ffmpeg: extract audio as 16kHz mono PCM to stdout
        cmd = [
            "ffmpeg",
            "-i", self._rtsp_url,
            "-vn",                    # no video
            "-acodec", "pcm_s16le",   # 16-bit PCM
            "-ar", str(SAMPLE_RATE),  # 16kHz
            "-ac", str(CHANNELS),     # mono
            "-f", "s16le",            # raw PCM output
            "-loglevel", "error",
            "pipe:1",
        ]
        self._process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        self._stream_started_at = time.time()
        logger.info("RTSP stream started, extracting audio via ffmpeg")
        self._schedule_extend()

    def start_recording(self, output_path: str) -> None:
        """Start recording the RTSP stream to a file (audio only)."""
        if not self._rtsp_url:
            return
        # Reuse the same RTSP URL (requesting a new stream can invalidate the first)
        cmd = [
            "ffmpeg",
            "-i", self._rtsp_url,
            "-vn",
            "-acodec", "aac",
            "-y",
            "-loglevel", "error",
            output_path,
        ]
        self._recording_process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        logger.info("Recording started: %s", output_path)

    def start_video_recording(self, output_path: str) -> None:
        """Start recording the RTSP stream as video+audio MP4."""
        if not self._rtsp_url:
            return
        cmd = [
            "ffmpeg",
            "-i", self._rtsp_url,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-c:a", "aac",
            "-movflags", "+faststart",
            "-y",
            "-loglevel", "error",
            output_path,
        ]
        self._video_process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        logger.info("Video recording started: %s", output_path)

    def stop_video_recording(self) -> None:
        """Stop the video recording process."""
        if self._video_process:
            self._video_process.terminate()
            try:
                self._video_process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._video_process.kill()
                self._video_process.wait(timeout=5)
            self._video_process = None
            logger.info("Video recording stopped")

    def stop_recording(self) -> str | None:
        """Stop the audio recording process."""
        if self._recording_process:
            self._recording_process.terminate()
            self._recording_process.wait(timeout=10)
            self._recording_process = None
            logger.info("Audio recording stopped")

    def read_frame(self, timeout: float = 30.0) -> bytes | None:
        """Read one YAMNet-sized audio frame (0.96s) from ffmpeg pipe.

        Uses non-blocking chunked reads so stalls are detected quickly
        rather than blocking in a single read() for minutes.

        Returns None if no data within timeout (stream likely dead).
        """
        if not self._process or not self._process.stdout:
            return None

        import os as _os
        import fcntl

        t0 = time.time()
        fd = self._process.stdout.fileno()

        # Set non-blocking mode for chunked reading
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | _os.O_NONBLOCK)

        try:
            buf = bytearray()
            while len(buf) < FRAME_BYTES:
                remaining_timeout = timeout - (time.time() - t0)
                if remaining_timeout <= 0:
                    logger.warning("read_frame timed out after %.0fs — stream may be dead", timeout)
                    return None

                # Wait for data with a short per-chunk timeout (5s)
                chunk_timeout = min(5.0, remaining_timeout)
                ready, _, _ = select.select([fd], [], [], chunk_timeout)
                if not ready:
                    # No data for 5s — stall detected
                    self._consecutive_slow += 1
                    if self._consecutive_slow >= 3:
                        logger.warning("RTSP stall: no data for %.0fs (consecutive_slow=%d)",
                                       time.time() - t0, self._consecutive_slow)
                        return None  # Trigger reconnect via caller
                    continue

                chunk = _os.read(fd, FRAME_BYTES - len(buf))
                if not chunk:
                    logger.warning("read_frame got EOF after %d/%d bytes", len(buf), FRAME_BYTES)
                    return None
                buf.extend(chunk)
                self._consecutive_slow = 0  # Got data, reset stall counter
        finally:
            # Restore blocking mode
            fcntl.fcntl(fd, fcntl.F_SETFL, flags)

        # Track overall read time
        elapsed = time.time() - t0
        if elapsed > 3.0:
            self._consecutive_slow += 1

        return bytes(buf)

    @property
    def needs_reconnect(self) -> bool:
        """True if stream has exceeded max age (stalls are caught in read_frame)."""
        if not self._stream_started_at:
            return False
        return (time.time() - self._stream_started_at) >= RECONNECT_INTERVAL

    def _schedule_extend(self):
        """Schedule stream extension before expiry."""
        if not self._active:
            return
        self._extend_timer = threading.Timer(EXTEND_INTERVAL, self._extend)
        self._extend_timer.daemon = True
        self._extend_timer.start()

    def _extend(self):
        """Extend the RTSP stream."""
        if not self._active or not self._stream_token:
            return
        try:
            result = self._sdm.extend_rtsp_stream(self._device_id, self._stream_token)
            self._stream_token = result.get("streamExtensionToken", self._stream_token)
            logger.info("RTSP stream extended")
            self._schedule_extend()
        except Exception:
            logger.exception("Failed to extend RTSP stream")

    def stop(self, release_stream: bool = True):
        """Stop the stream and clean up.

        Args:
            release_stream: If True, also release the server-side stream via API.
                           Set False when you plan to reconnect immediately.
        """
        self._active = False
        if self._extend_timer:
            self._extend_timer.cancel()
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("ffmpeg didn't terminate, killing")
                self._process.kill()
                self._process.wait(timeout=5)
            self._process = None
        if self._recording_process:
            self._recording_process.terminate()
            try:
                self._recording_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("Recording ffmpeg didn't terminate, killing")
                self._recording_process.kill()
                self._recording_process.wait(timeout=5)
            self._recording_process = None
        if self._video_process:
            self._video_process.terminate()
            try:
                self._video_process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._video_process.kill()
                self._video_process.wait(timeout=5)
            self._video_process = None
        if release_stream and self._stream_token:
            try:
                self._sdm.stop_rtsp_stream(self._device_id, self._stream_token)
            except Exception:
                logger.exception("Failed to stop RTSP stream via API")
        self._stream_token = None
        self._rtsp_url = None
        logger.info("RTSP stream stopped (release=%s)", release_stream)
