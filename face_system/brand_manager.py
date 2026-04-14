"""
BrandManager
============
Tracks unique visitor counts and detection events per brand (store/business).

A brand is a logical grouping of cameras (e.g. all cameras inside one store).

Key invariant:
    The same person detected on multiple cameras belonging to the SAME brand
    is counted as ONE unique visitor for that brand.

This is enforced because FaceDatabase stores embeddings per brand, so
face_id deduplication already operates at brand scope.

Data model (per brand):
    {
        "name":         str,          # human-readable brand name
        "unique_count": int,          # total unique visitors detected
        "face_ids":     list[str],    # stored face IDs seen for this brand
        "camera_ids":   set[int],     # cameras that have reported detections
        "detections":   int,          # total detection events (including repeats)
    }
"""

import logging
import threading
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class BrandStats:
    name:         str
    unique_count: int                = 0
    face_ids:     list[str]          = field(default_factory=list)
    camera_ids:   set[int]           = field(default_factory=set)
    detections:   int                = 0   # total events including repeats

    def to_dict(self) -> dict:
        return {
            "name":         self.name,
            "unique_count": self.unique_count,
            "face_ids":     list(self.face_ids),
            "camera_ids":   sorted(self.camera_ids),
            "detections":   self.detections,
        }


class BrandManager:
    """Maintain per-brand visitor statistics.

    Args:
        brands:  ``{brand_id: brand_name}`` mapping.
        cameras: List of camera config dicts, each with keys:
                 ``camera_id``, ``camera_name``, ``brand_id``.

    Example::

        brands  = {101: "Brand_A", 202: "Brand_B"}
        cameras = [
            {"camera_id": 1, "camera_name": "Entrance Cam", "brand_id": 101},
            {"camera_id": 2, "camera_name": "Checkout Cam",  "brand_id": 101},
            {"camera_id": 3, "camera_name": "Hall Cam",      "brand_id": 202},
        ]
        mgr = BrandManager(brands, cameras)
    """

    def __init__(
        self,
        brands:  dict[int, str],
        cameras: list[dict],
    ):
        self._lock = threading.Lock()

        # brand_id → BrandStats
        self._stats: dict[int, BrandStats] = {
            bid: BrandStats(name=name)
            for bid, name in brands.items()
        }

        # camera_id → brand_id  (fast lookup)
        self._cam_to_brand: dict[int, int] = {
            cam["camera_id"]: cam["brand_id"]
            for cam in cameras
        }

        # camera_id → camera metadata
        self._cameras: dict[int, dict] = {
            cam["camera_id"]: cam for cam in cameras
        }

        log.info(
            f"[BrandManager] Initialised with {len(brands)} brand(s) "
            f"and {len(cameras)} camera(s)"
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def brand_for_camera(self, camera_id: int) -> Optional[int]:
        """Return the brand_id for a given camera, or None if unknown."""
        return self._cam_to_brand.get(camera_id)

    def register_detection(
        self,
        camera_id: int,
        face_id:   str,
        is_new:    bool,
    ) -> Optional[BrandStats]:
        """Record a face detection event.

        Args:
            camera_id: ID of the camera that saw the face.
            face_id:   Face ID returned by FaceDatabase.process_face().
            is_new:    True if FaceDatabase classified this as a new unique person.

        Returns:
            Updated BrandStats for the relevant brand, or None if camera unknown.
        """
        brand_id = self.brand_for_camera(camera_id)
        if brand_id is None:
            log.warning(f"[BrandManager] Unknown camera_id={camera_id}")
            return None

        with self._lock:
            stats = self._stats[brand_id]
            stats.detections  += 1
            stats.camera_ids.add(camera_id)

            if is_new:
                stats.unique_count += 1
                stats.face_ids.append(face_id)
                log.info(
                    f"[BrandManager] New visitor — brand='{stats.name}' "
                    f"face={face_id} cam={camera_id} "
                    f"total_unique={stats.unique_count}"
                )
            else:
                log.debug(
                    f"[BrandManager] Repeat visitor — brand='{stats.name}' "
                    f"face={face_id} (not counted again)"
                )

        return stats

    def get_brand_stats(self, brand_id: int) -> Optional[dict]:
        """Return stats dict for a single brand."""
        stats = self._stats.get(brand_id)
        return stats.to_dict() if stats else None

    def get_all_stats(self) -> dict[int, dict]:
        """Return stats dict for every brand."""
        with self._lock:
            return {bid: s.to_dict() for bid, s in self._stats.items()}

    def summary(self) -> str:
        """Return a human-readable multi-line summary string."""
        lines = ["=== Brand Statistics ==========================="]
        for bid, s in self._stats.items():
            lines.append(
                f"  [{bid}] {s.name:20s} "
                f"unique={s.unique_count:4d}  "
                f"events={s.detections:5d}  "
                f"cams={sorted(s.camera_ids)}"
            )
        lines.append("================================================")
        return "\n".join(lines)
