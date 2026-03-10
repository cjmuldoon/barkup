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

# YAMNet class indices for sounds that suppress bark detections.
# If the highest score among these classes exceeds the bark score,
# the detection is likely speech/music/TV/impact rather than a real bark.
SUPPRESS_CLASSES = {
    # Speech & voice
    0,    # Speech
    1,    # Child speech, kid speaking
    2,    # Conversation
    3,    # Narration, monologue
    4,    # Babbling
    5,    # Speech synthesizer
    6,    # Shout
    10,   # Singing
    11,   # Choir
    # Music & media
    137,  # Music
    138,  # Musical instrument
    180,  # Brass instrument
    181,  # French horn
    182,  # Trumpet
    183,  # Trombone
    190,  # Wind instrument / woodwind
    191,  # Flute
    192,  # Saxophone
    193,  # Clarinet
    289,  # Television
    290,  # Radio
    291,  # Video game music
    # Appliances
    363,  # Blender
    406,  # Mechanical fan
    482,  # Whir
    # Impact & household noises
    348,  # Door
    352,  # Slam
    353,  # Knock
    356,  # Cupboard open or close
    357,  # Drawer open or close
    358,  # Dishes, pots, and pans
    454,  # Thump, thud
    455,  # Thunk
    460,  # Bang
    461,  # Slap, smack
    462,  # Whack, thwack
    463,  # Smash, crash
    464,  # Breaking
    483,  # Clatter
}

# Readable names for classes we care about (for logging)
CLASS_NAMES = {
    0: "Speech", 1: "Child speech", 2: "Conversation", 3: "Narration",
    4: "Babbling", 5: "Speech synth", 6: "Shout", 10: "Singing",
    11: "Choir", 67: "Domestic animal", 68: "Dog (generic)",
    69: "Dog", 70: "Bark", 71: "Yip", 72: "Howl",
    74: "Growling", 75: "Whimper", 137: "Music", 138: "Instrument",
    180: "Brass", 181: "French horn", 182: "Trumpet", 183: "Trombone",
    190: "Woodwind", 191: "Flute", 192: "Saxophone", 193: "Clarinet",
    289: "Television", 290: "Radio", 291: "Game music",
    363: "Blender", 406: "Mech fan", 482: "Whir",
    348: "Door", 352: "Slam", 353: "Knock", 356: "Cupboard",
    357: "Drawer", 358: "Dishes/pans", 454: "Thump", 455: "Thunk",
    460: "Bang", 461: "Slap", 462: "Whack", 463: "Crash",
    464: "Breaking", 483: "Clatter", 494: "Silence",
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
        self._frame_count = 0
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

        # Negative class filter: suppress if speech/music/TV scores higher
        suppressed = False
        if is_bark:
            suppress_score = max(float(scores[i]) for i in SUPPRESS_CLASSES)
            if suppress_score > best_confidence:
                suppress_class = max(SUPPRESS_CLASSES, key=lambda i: scores[i])
                logger.info(
                    "Bark suppressed: bark=%.3f, %s=%.3f",
                    best_confidence,
                    CLASS_NAMES.get(suppress_class, f"class {suppress_class}"),
                    suppress_score,
                )
                is_bark = False
                suppressed = True

        # Log every frame so we can see what YAMNet is hearing
        self._frame_count += 1
        top_class = int(np.argmax(scores))
        top_name = CLASS_NAMES.get(top_class, f"class {top_class}")
        top_score = float(scores[top_class])
        if is_bark:
            logger.info(
                "Frame %d: BARK detected (%.3f, %s) | top: %s=%.3f",
                self._frame_count, best_confidence, best_type.value,
                top_name, top_score,
            )
        elif suppressed:
            pass  # Already logged above
        elif self._frame_count % 10 == 0:
            # Log every 10th non-bark frame to avoid spam
            dog_str = f"dog={best_confidence:.3f}" if best_confidence > 0.01 else ""
            logger.info(
                "Frame %d: %s=%.3f %s",
                self._frame_count, top_name, top_score, dog_str,
            )

        return BarkDetection(
            timestamp=datetime.now(),
            is_bark=is_bark,
            confidence=best_confidence,
            bark_type=best_type if is_bark else None,
        )
