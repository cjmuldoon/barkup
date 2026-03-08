#!/usr/bin/env python3
"""Download the YAMNet TFLite model."""

import os
import urllib.request
from pathlib import Path

YAMNET_URL = "https://storage.googleapis.com/tfhub-lite-models/google/lite-model/yamnet/classification/tflite/1.tflite"
MODEL_DIR = Path(__file__).parent.parent / "models"
MODEL_PATH = MODEL_DIR / "yamnet.tflite"


def main():
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    if MODEL_PATH.exists():
        print(f"Model already exists at {MODEL_PATH}")
        return

    print(f"Downloading YAMNet TFLite model...")
    urllib.request.urlretrieve(YAMNET_URL, MODEL_PATH)
    size_mb = MODEL_PATH.stat().st_size / (1024 * 1024)
    print(f"Downloaded to {MODEL_PATH} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
