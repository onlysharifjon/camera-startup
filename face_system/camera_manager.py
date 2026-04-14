"""
CameraManager
=============
Manages multiple simultaneous camera streams (RTSP or webcam index).

Each camera runs in its own daemon thread:
    read frame → detect faces → crop → embed → dedup → record stats

Thread safety:
    FaceDatabase uses an internal write lock.
    BrandManager uses an internal write lock.
    Frame buffers are protected per-stream.

Detection interval:
    To avoid hammering the embedding model, face detection + dedup runs
    at most once per `detect_interval` seconds (default 1.0).
    The latest raw frame is always available for preview/streaming.
"""

import logging
import threading
import time
from typing import Optional

import cv2
import numpy as np

from .face_detector import FaceDetector
from .face_database import FaceDatabase
from .brand_manager import BrandManager

log = logging.getLogger(__name__)


class _CameraStream:
    """Internal: one thread per camera."""

    def __init__(
        self,
        config:          dict,
        detector:        FaceDetector,
        face_db:         FaceDatabase,
        brand_mgr:       BrandManager,
        detect_interval: float,
        on_detection,       # callable(camera_id, faces_info) | None
    ):
        self.config    = config
        self.cam_id:   int = config["camera_id"]
        self.cam_name: str = config["camera_name"]
        self.brand_id: int = config["brand_id"]

        # Source: int → webcam index, str → RTSP URL
        self.source = config.get("source", self.cam_id)

        self._detector        = detector
        self._face_db         = face_db
        self._brand_mgr       = brand_mgr
        self._detect_interval = detect_interval
        self._on_detection    = on_detection

        self.active = False
        self._thread: Optional[threading.Thread] = None
        self._lock   = threading.Lock()

        # Latest frames (raw and annotated)
        self._latest_frame:     Optional[np.ndarray] = None
        self._latest_annotated: Optional[np.ndarray] = None
        self._latest_faces:     list[dict]            = []

        # Counters
        self.total_detections = 0
        self.unique_detections = 0

    # ── Thread lifecycle ───────────────────────────────────────────────────────

    def start(self):
        if self.active:
            return
        self.active  = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name=f"cam-{self.cam_id}"
        )
        self._thread.start()
        log.info(f"[CameraManager] Stream started: [{self.cam_id}] {self.cam_name}")

    def stop(self):
        self.active = False
        log.info(f"[CameraManager] Stream stopping: [{self.cam_id}] {self.cam_name}")

    # ── Frame accessors ────────────────────────────────────────────────────────

    def latest_frame(self) -> Optional[bytes]:
        """Return the latest raw frame encoded as JPEG bytes."""
        with self._lock:
            frame = self._latest_frame
        if frame is None:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes() if ok else None

    def latest_annotated(self) -> Optional[bytes]:
        """Return the latest annotated frame (face boxes) as JPEG bytes."""
        with self._lock:
            frame = self._latest_annotated or self._latest_frame
        if frame is None:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return buf.tobytes() if ok else None

    def latest_faces(self) -> list[dict]:
        with self._lock:
            return list(self._latest_faces)

    # ── Main processing loop ───────────────────────────────────────────────────

    def _run(self):
        retry_delay  = 5
        last_detect  = 0.0

        while self.active:
            cap = cv2.VideoCapture(self.source)

            if not cap.isOpened():
                log.warning(
                    f"[CameraManager] Cannot open source '{self.source}' "
                    f"for cam {self.cam_id} – retrying in {retry_delay}s"
                )
                time.sleep(retry_delay)
                continue

            # Keep buffer small to stay near real-time
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            log.info(f"[CameraManager] Connected to source for cam {self.cam_id}")

            while self.active:
                ret, frame = cap.read()
                if not ret:
                    log.warning(
                        f"[CameraManager] Frame read failed for cam {self.cam_id} "
                        f"– reconnecting"
                    )
                    break

                # Always update latest raw frame
                with self._lock:
                    self._latest_frame = frame

                # ── Face detection (throttled by detect_interval) ──────────────
                now = time.time()
                if now - last_detect < self._detect_interval:
                    continue
                last_detect = now

                self._process_frame(frame)

            cap.release()
            if self.active:
                log.info(
                    f"[CameraManager] Reconnecting cam {self.cam_id} "
                    f"in {retry_delay}s"
                )
                time.sleep(retry_delay)

    def _process_frame(self, frame: np.ndarray):
        """Run full detection pipeline on one frame."""
        raw_faces = self._detector.detect(frame)

        faces_info = []   # enriched face dicts returned to caller

        for face in raw_faces:
            crop = self._detector.crop(frame, face["box"])
            if crop is None:
                continue

            # Dedup + storage (per brand)
            is_new, face_id, embedding = self._face_db.process_face(
                crop, self.brand_id
            )

            # Record in brand manager
            self._brand_mgr.register_detection(self.cam_id, face_id, is_new)

            self.total_detections  += 1
            if is_new:
                self.unique_detections += 1

            face_info = {
                **face,
                "face_id":  face_id,
                "is_new":   is_new,
                "label":    face_id if is_new else f"↩ {face_id}",
                "brand_id": self.brand_id,
            }
            faces_info.append(face_info)

        # Annotate frame with boxes + people count
        annotated = self._detector.annotate(frame, faces_info)

        with self._lock:
            self._latest_faces     = faces_info
            self._latest_annotated = annotated

        # Fire optional callback
        if self._on_detection and faces_info:
            try:
                self._on_detection(self.cam_id, faces_info)
            except Exception as e:
                log.warning(f"[CameraManager] on_detection callback error: {e}")


# ── Public class ───────────────────────────────────────────────────────────────

class CameraManager:
    """Orchestrates multiple camera streams with shared detector + database.

    Args:
        cameras:         List of camera config dicts.
                         Required keys: camera_id, camera_name, brand_id.
                         Optional key:  source (int for webcam, str for RTSP).
                         If ``source`` is omitted, ``camera_id`` is used as the
                         webcam device index.
        brands:          ``{brand_id: brand_name}`` mapping.
        db_root:         Root directory for face storage.
        dedup_threshold: Euclidean distance threshold for face deduplication.
        detect_interval: Minimum seconds between detection runs per camera.
        on_detection:    Optional callback ``(camera_id: int, faces: list)``
                         fired after each detection batch.

    Example::

        cameras = [
            {"camera_id": 1, "camera_name": "Entrance Cam",
             "brand_id": 101, "source": "rtsp://..."},
            {"camera_id": 2, "camera_name": "Checkout Cam",
             "brand_id": 101, "source": 0},
        ]
        brands = {101: "Brand_A", 202: "Brand_B"}

        mgr = CameraManager(cameras=cameras, brands=brands)
        mgr.start_all()
    """

    def __init__(
        self,
        cameras:          list[dict],
        brands:           dict[int, str],
        db_root:          str = "faces_db",
        dedup_threshold:  float = 0.6,
        detect_interval:  float = 1.0,
        on_detection=None,
    ):
        self._detector  = FaceDetector()
        self._face_db   = FaceDatabase(db_root=db_root, threshold=dedup_threshold)
        self._brand_mgr = BrandManager(brands=brands, cameras=cameras)

        self._streams: dict[int, _CameraStream] = {}

        for cam in cameras:
            stream = _CameraStream(
                config          = cam,
                detector        = self._detector,
                face_db         = self._face_db,
                brand_mgr       = self._brand_mgr,
                detect_interval = detect_interval,
                on_detection    = on_detection,
            )
            self._streams[cam["camera_id"]] = stream

        log.info(
            f"[CameraManager] Initialised with {len(self._streams)} camera(s)"
        )

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start_all(self):
        """Start all camera stream threads."""
        for stream in self._streams.values():
            stream.start()

    def stop_all(self):
        """Signal all camera threads to stop."""
        for stream in self._streams.values():
            stream.stop()

    def start_camera(self, camera_id: int):
        """Start a single camera by ID."""
        stream = self._streams.get(camera_id)
        if stream:
            stream.start()
        else:
            log.warning(f"[CameraManager] Unknown camera_id={camera_id}")

    def stop_camera(self, camera_id: int):
        """Stop a single camera by ID."""
        stream = self._streams.get(camera_id)
        if stream:
            stream.stop()

    # ── Frame access ───────────────────────────────────────────────────────────

    def get_frame(self, camera_id: int) -> Optional[bytes]:
        """Return latest raw JPEG bytes for a camera."""
        s = self._streams.get(camera_id)
        return s.latest_frame() if s else None

    def get_annotated_frame(self, camera_id: int) -> Optional[bytes]:
        """Return latest annotated JPEG bytes (face boxes) for a camera."""
        s = self._streams.get(camera_id)
        return s.latest_annotated() if s else None

    def get_faces(self, camera_id: int) -> list[dict]:
        """Return latest detected faces for a camera."""
        s = self._streams.get(camera_id)
        return s.latest_faces() if s else []

    # ── Statistics ─────────────────────────────────────────────────────────────

    def brand_stats(self) -> dict[int, dict]:
        """Return per-brand visitor statistics."""
        return self._brand_mgr.get_all_stats()

    def camera_stats(self) -> list[dict]:
        """Return per-camera detection counters."""
        return [
            {
                "camera_id":        s.cam_id,
                "camera_name":      s.cam_name,
                "brand_id":         s.brand_id,
                "active":           s.active,
                "total_detections": s.total_detections,
                "unique_new":       s.unique_detections,
            }
            for s in self._streams.values()
        ]

    def summary(self) -> str:
        """Print-ready summary of brand statistics."""
        return self._brand_mgr.summary()
