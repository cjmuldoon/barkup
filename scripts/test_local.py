#!/usr/bin/env python3
"""Quick local test to verify all components work."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np


def test_classifier():
    """Test YAMNet model loads and classifies audio."""
    print("1. Testing bark classifier...")
    from barkup.bark_classifier import BarkClassifier

    classifier = BarkClassifier()

    # Generate a silent frame (16kHz, 16-bit PCM, model's expected size)
    silence = np.zeros(classifier.frame_samples, dtype=np.int16).tobytes()
    result = classifier.classify_frame(silence)
    print(f"   Silence -> is_bark={result.is_bark}, confidence={result.confidence:.4f}")
    assert not result.is_bark, "Silence should not be classified as bark"

    # Generate a noisy frame (random noise)
    noise = np.random.randint(-5000, 5000, classifier.frame_samples, dtype=np.int16).tobytes()
    result = classifier.classify_frame(noise)
    print(f"   Noise   -> is_bark={result.is_bark}, confidence={result.confidence:.4f}")

    print("   PASSED: Classifier loads and runs inference\n")


def test_sdm_connection():
    """Test Google SDM API connection."""
    print("2. Testing Google SDM API connection...")
    from barkup.sdm_client import SDMClient

    sdm = SDMClient()
    devices = sdm.list_devices()
    print(f"   Found {len(devices)} device(s)")
    for d in devices:
        dtype = d.get("type", "unknown")
        traits = list(d.get("traits", {}).keys())
        print(f"   - {dtype}: {', '.join(traits)}")
    assert len(devices) > 0, "No devices found"
    print("   PASSED: SDM API connected\n")


def test_notion_connection():
    """Test Notion API connection."""
    print("3. Testing Notion API connection...")
    from notion_client import Client
    from barkup.config import settings

    client = Client(auth=settings.notion_api_key)
    db = client.databases.retrieve(database_id=settings.notion_database_id)
    title = db.get("title", [{}])[0].get("plain_text", "Unknown")
    print(f"   Database: {title}")
    print(f"   Properties: {', '.join(db.get('properties', {}).keys())}")
    print("   PASSED: Notion API connected\n")


def test_episode_tracker():
    """Test episode tracker state machine."""
    print("4. Testing episode tracker...")
    from datetime import datetime, timedelta
    from barkup.episode_tracker import EpisodeTracker
    from barkup.models import BarkDetection, BarkType

    tracker = EpisodeTracker()
    base = datetime(2024, 1, 1)

    # Simulate 5 barks then 35s silence
    for i in range(5):
        tracker.process(BarkDetection(
            timestamp=base + timedelta(seconds=i),
            is_bark=True, confidence=0.7, bark_type=BarkType.BARK,
        ))

    assert tracker.state == "BARKING"

    episode = None
    for i in range(35):
        ep = tracker.process(BarkDetection(
            timestamp=base + timedelta(seconds=5 + i),
            is_bark=False, confidence=0.05, bark_type=None,
        ))
        if ep:
            episode = ep

    assert episode is not None
    print(f"   Episode: {episode.duration_seconds}s, {episode.bark_frame_count} bark frames")
    print(f"   Peak confidence: {episode.peak_confidence}, type: {episode.dominant_bark_type.value}")
    print("   PASSED: Episode tracker works\n")


if __name__ == "__main__":
    print("=" * 50)
    print("Barkup Local Test")
    print("=" * 50 + "\n")

    passed = 0
    failed = 0

    for test in [test_classifier, test_sdm_connection, test_notion_connection, test_episode_tracker]:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"   FAILED: {e}\n")
            failed += 1

    print("=" * 50)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 50)
