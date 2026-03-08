from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class BarkType(str, Enum):
    BARK = "Bark"
    HOWL = "Howl"
    YIP = "Yip"
    WHIMPER = "Whimper"
    GROWL = "Growl"


class BarkDetection(BaseModel):
    timestamp: datetime
    is_bark: bool
    confidence: float
    bark_type: BarkType | None = None


class Episode(BaseModel):
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    bark_frame_count: int
    total_frames: int
    peak_confidence: float
    dominant_bark_type: BarkType
    snapshot_url: str | None = None
    clip_path: str | None = None
    clip_url: str | None = None
    nest_link: str | None = None
