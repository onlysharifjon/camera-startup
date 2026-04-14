"""
face_system
===========
Multi-camera face detection, deduplication, and brand-level visitor tracking.

Modules:
    face_detector   – Haar Cascade detection + face cropping
    face_database   – Per-brand face storage with embedding deduplication
    brand_manager   – Brand/store-level unique visitor statistics
    camera_manager  – Multi-threaded RTSP / webcam stream processing
"""

from .face_detector  import FaceDetector
from .face_database  import FaceDatabase
from .brand_manager  import BrandManager
from .camera_manager import CameraManager

__all__ = ["FaceDetector", "FaceDatabase", "BrandManager", "CameraManager"]
