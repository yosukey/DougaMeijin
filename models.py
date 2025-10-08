# models.py
import uuid
from dataclasses import dataclass, field
from typing import List, Optional
from config import DEFAULT_RESOLUTION, DEFAULT_FPS

@dataclass
class Page:
    image: str
    page_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    original_filename: Optional[str] = None
    pdf_page_number: Optional[int] = None
    original_resolution: Optional[str] = None
    exif_orientation: Optional[str] = None
    audio: Optional[str] = None
    duration: Optional[float] = None
    locked: bool = False
    audio_source_info: Optional[str] = None

@dataclass
class Project:
    version: int = 1
    resolution: str = DEFAULT_RESOLUTION
    fps: int = DEFAULT_FPS
    transition: str = "none"
    pages: List[Page] = field(default_factory=list)

    def ensure_bounds(self):
        if self.resolution not in ("1080p", "720p"):
            self.resolution = DEFAULT_RESOLUTION
        if self.fps <= 0:
            self.fps = DEFAULT_FPS
