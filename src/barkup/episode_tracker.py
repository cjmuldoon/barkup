"""State machine that groups individual bark detections into episodes."""

import logging
from collections import Counter
from datetime import datetime

from barkup.config import settings
from barkup.models import BarkDetection, BarkType, Episode

logger = logging.getLogger(__name__)


class EpisodeTracker:
    """
    State machine: IDLE -> PENDING -> BARKING -> COOLDOWN -> IDLE
                                              -> BARKING (if bark during cooldown)

    PENDING requires MIN_CONSECUTIVE_BARKS consecutive bark frames before
    confirming as a real episode. Single bangs/impacts get discarded.
    """

    MIN_CONSECUTIVE_BARKS = 3  # ~3 seconds of sustained barking to confirm

    def __init__(self, event_timestamp: datetime | None = None):
        self._state = "IDLE"
        # Strip timezone to keep all datetimes naive (consistent with datetime.now())
        if event_timestamp and event_timestamp.tzinfo is not None:
            event_timestamp = event_timestamp.replace(tzinfo=None)
        self._event_timestamp = event_timestamp
        self._episode_start: datetime | None = None
        self._last_bark_time: datetime | None = None
        self._bark_frame_count = 0
        self._total_frames = 0
        self._peak_confidence = 0.0
        self._bark_types: list[BarkType] = []
        self._consecutive_barks = 0

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_active(self) -> bool:
        return self._state != "IDLE"

    def process(self, detection: BarkDetection) -> Episode | None:
        """
        Process a bark detection. Returns an Episode if one just completed.
        """
        self._total_frames += 1
        now = detection.timestamp

        if detection.is_bark:
            self._consecutive_barks += 1
            self._peak_confidence = max(self._peak_confidence, detection.confidence)
            self._bark_frame_count += 1
            self._last_bark_time = now
            if detection.bark_type:
                self._bark_types.append(detection.bark_type)

            if self._state == "IDLE":
                # First bark frame — enter pending, wait for consecutive confirmation
                self._state = "PENDING"
                if self._event_timestamp:
                    self._episode_start = self._event_timestamp
                    self._event_timestamp = None  # Only use for first episode
                else:
                    self._episode_start = now

            elif self._state == "PENDING":
                # Check if we've hit the consecutive threshold
                if self._consecutive_barks >= self.MIN_CONSECUTIVE_BARKS:
                    self._state = "BARKING"
                    logger.info("Episode confirmed at %s (%d consecutive bark frames)",
                                self._episode_start, self._consecutive_barks)

            elif self._state == "COOLDOWN":
                # Back to barking
                self._state = "BARKING"

        else:
            self._consecutive_barks = 0

            if self._state == "PENDING":
                # Bark didn't sustain — discard (likely a bang/impact)
                logger.info("Pending episode discarded (only %d consecutive bark frames)",
                            self._bark_frame_count)
                self._reset()

            elif self._state == "BARKING":
                # No bark detected while barking -> enter cooldown
                self._state = "COOLDOWN"

            elif self._state == "COOLDOWN":
                # Check if cooldown has expired
                if self._last_bark_time:
                    elapsed = (now - self._last_bark_time).total_seconds()
                    if elapsed >= settings.episode_cooldown_seconds:
                        return self._finalize_episode()

        return None

    def force_end(self) -> Episode | None:
        """Force-end current episode (e.g., when stream stops)."""
        if self._state == "PENDING":
            # Never confirmed — discard
            self._reset()
            return None
        if self._state != "IDLE" and self._episode_start:
            return self._finalize_episode()
        return None

    def _finalize_episode(self) -> Episode:
        """Create an Episode from accumulated data and reset."""
        end_time = self._last_bark_time or datetime.now()
        duration = (end_time - self._episode_start).total_seconds()

        # Find dominant bark type
        type_counts = Counter(self._bark_types)
        dominant_type = (
            type_counts.most_common(1)[0][0] if type_counts else BarkType.BARK
        )

        episode = Episode(
            start_time=self._episode_start,
            end_time=end_time,
            duration_seconds=round(duration, 1),
            bark_frame_count=self._bark_frame_count,
            total_frames=self._total_frames,
            peak_confidence=round(self._peak_confidence, 3),
            dominant_bark_type=dominant_type,
        )

        logger.info(
            "Episode ended: %.1fs, %d bark frames, peak=%.3f, type=%s",
            duration,
            self._bark_frame_count,
            self._peak_confidence,
            dominant_type.value,
        )

        self._reset()
        return episode

    def _reset(self):
        self._state = "IDLE"
        self._episode_start = None
        self._last_bark_time = None
        self._bark_frame_count = 0
        self._total_frames = 0
        self._peak_confidence = 0.0
        self._bark_types = []
        self._consecutive_barks = 0
