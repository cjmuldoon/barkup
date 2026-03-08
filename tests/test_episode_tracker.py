"""Tests for the episode tracker state machine."""

from datetime import datetime, timedelta

from barkup.episode_tracker import EpisodeTracker
from barkup.models import BarkDetection, BarkType


def _detection(is_bark: bool, seconds_offset: float = 0, confidence: float = 0.5):
    return BarkDetection(
        timestamp=datetime(2024, 1, 1) + timedelta(seconds=seconds_offset),
        is_bark=is_bark,
        confidence=confidence if is_bark else 0.05,
        bark_type=BarkType.BARK if is_bark else None,
    )


def test_single_bark_episode():
    tracker = EpisodeTracker()
    results = []

    # Bark for 5 seconds (each frame is ~1s)
    for i in range(5):
        ep = tracker.process(_detection(True, seconds_offset=i))
        if ep:
            results.append(ep)

    assert tracker.state == "BARKING"
    assert len(results) == 0  # Not finished yet

    # Silence for cooldown period (default 30s)
    for i in range(35):
        ep = tracker.process(_detection(False, seconds_offset=5 + i))
        if ep:
            results.append(ep)

    assert len(results) == 1
    assert results[0].bark_frame_count == 5
    assert results[0].dominant_bark_type == BarkType.BARK


def test_bark_resumes_during_cooldown():
    tracker = EpisodeTracker()

    # Bark, pause, bark again within cooldown
    tracker.process(_detection(True, 0))
    tracker.process(_detection(True, 1))
    tracker.process(_detection(False, 2))
    tracker.process(_detection(False, 3))
    # Still in cooldown, bark again
    tracker.process(_detection(True, 5))

    assert tracker.state == "BARKING"
    assert tracker._bark_frame_count == 3


def test_force_end():
    tracker = EpisodeTracker()

    tracker.process(_detection(True, 0))
    tracker.process(_detection(True, 1))

    episode = tracker.force_end()
    assert episode is not None
    assert episode.bark_frame_count == 2
    assert tracker.state == "IDLE"


def test_no_barks_stays_idle():
    tracker = EpisodeTracker()

    for i in range(10):
        ep = tracker.process(_detection(False, i))
        assert ep is None

    assert tracker.state == "IDLE"
