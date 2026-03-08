"""YAMNet-based bark classifier using TFLite."""

import logging
import os
from datetime import datetime
from pathlib import Path

import numpy as np

from barkup.config import settings
from barkup.models import BarkDetection, BarkType

logger = logging.getLogger(__name__)

# YAMNet class indices for dog-related sounds
DOG_CLASSES = {
    69: BarkType.BARK,    # Dog
    70: BarkType.BARK,    # Bark
    71: BarkType.YIP,     # Yip
    72: BarkType.HOWL,    # Howl
    74: BarkType.GROWL,   # Growling
    75: BarkType.WHIMPER, # Whimper
}

MODEL_PATH = os.environ.get(
    "YAMNET_MODEL_PATH",
    str(Path(__file__).parent.parent.parent / "models" / "yamnet.tflite"),
)


class BarkClassifier:
    def __init__(self):
        self._interpreter = None
        self._input_details = None
        self._output_details = None
        self._load_model()

    def _load_model(self):
        """Load YAMNet TFLite model."""
        try:
            from ai_edge_litert.interpreter import Interpreter
        except ImportError:
            try:
                from tflite_runtime.interpreter import Interpreter
            except ImportError:
                import tensorflow as tf
                Interpreter = tf.lite.Interpreter

        self._interpreter = Interpreter(model_path=MODEL_PATH)
        self._interpreter.allocate_tensors()
        self._input_details = self._interpreter.get_input_details()
        self._output_details = self._interpreter.get_output_details()
        # Read the actual expected input size from the model
        self.frame_samples = self._input_details[0]["shape"][0]
        logger.info(
            "YAMNet model loaded from %s (frame size: %d samples)",
            MODEL_PATH, self.frame_samples,
        )

    def classify_frame(self, pcm_bytes: bytes) -> BarkDetection:
        """
        Classify a single audio frame.

        Args:
            pcm_bytes: Raw 16-bit PCM audio, 16kHz mono.

        Returns:
            BarkDetection with classification results.
        """
        # Convert 16-bit PCM to float32 in [-1, 1]
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        # Pad or trim to match model's expected input size
        if len(audio) < self.frame_samples:
            audio = np.pad(audio, (0, self.frame_samples - len(audio)))
        elif len(audio) > self.frame_samples:
            audio = audio[:self.frame_samples]

        # Run inference
        self._interpreter.set_tensor(self._input_details[0]["index"], audio)
        self._interpreter.invoke()
        scores = self._interpreter.get_tensor(self._output_details[0]["index"])[0]

        # Check dog-related classes
        best_confidence = 0.0
        best_type = None
        for class_idx, bark_type in DOG_CLASSES.items():
            if scores[class_idx] > best_confidence:
                best_confidence = float(scores[class_idx])
                best_type = bark_type

        is_bark = best_confidence >= settings.bark_confidence_threshold

        return BarkDetection(
            timestamp=datetime.now(),
            is_bark=is_bark,
            confidence=best_confidence,
            bark_type=best_type if is_bark else None,
        )
