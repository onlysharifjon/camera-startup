"""
FaceDatabase
============
Per-brand face storage with embedding-based deduplication.

Storage layout on disk:
    faces_db/
        brand_101/
            encodings.pkl       ← serialised list of (face_id, np.ndarray)
            face_0001.jpg
            face_0002.jpg
        brand_202/
            encodings.pkl
            face_0001.jpg

Deduplication algorithm:
    1. Generate a 128-dim L2-normalised embedding for the new face crop.
    2. Compute Euclidean distance between the new embedding and every stored
       embedding for the same brand.
    3. If the minimum distance is below `threshold` (default 0.6), the face
       is considered a duplicate.  Otherwise it is a new unique person.

Embedding back-end (tried in order):
    • face_recognition  (dlib ResNet-128, preferred)
    • DeepFace Facenet  (Keras/TF, always available here)
"""

import logging
import pickle
import threading
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)

# ── Embedding back-end detection ───────────────────────────────────────────────

try:
    import face_recognition as _fr
    _BACKEND = "face_recognition"
    log.info("[FaceDatabase] Using face_recognition (dlib) for embeddings")
except ImportError:
    _fr = None
    try:
        from deepface import DeepFace as _df
        _BACKEND = "deepface"
        log.info("[FaceDatabase] face_recognition not available – using DeepFace/Facenet")
    except ImportError:
        _df = None
        _BACKEND = "none"
        log.warning("[FaceDatabase] No embedding backend available – dedup disabled")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _l2_normalize(vec: np.ndarray) -> np.ndarray:
    """Return L2-normalised vector (safe against zero-norm)."""
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 1e-8 else vec


def _euclidean(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a - b))


# ── Temporary file path for DeepFace (requires a file, not an ndarray) ─────────
_TMP_FACE_PATH = Path("faces_db") / "_tmp_face.jpg"


class FaceDatabase:
    """Brand-scoped face store with embedding deduplication.

    Args:
        db_root:   Root directory for all brand face folders.
                   Defaults to ``faces_db/`` in the current working directory.
        threshold: Euclidean distance threshold between L2-normalised 128-dim
                   embeddings.  Faces closer than this are treated as the same
                   person.  Default 0.6 works well for both dlib and Facenet.
    """

    def __init__(self, db_root: str | Path = "faces_db", threshold: float = 0.6):
        self._root = Path(db_root)
        self._root.mkdir(parents=True, exist_ok=True)

        self._threshold = threshold

        # In-memory cache: brand_id -> list of (face_id, np.ndarray)
        self._cache: dict[int, list[tuple[str, np.ndarray]]] = {}
        self._lock = threading.Lock()   # serialise writes across camera threads

        log.info(f"[FaceDatabase] Storage root: {self._root.resolve()}")

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _brand_dir(self, brand_id: int) -> Path:
        d = self._root / f"brand_{brand_id}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _encodings_path(self, brand_id: int) -> Path:
        return self._brand_dir(brand_id) / "encodings.pkl"

    def _load_encodings(self, brand_id: int) -> list[tuple[str, np.ndarray]]:
        """Load encodings from disk into cache (called once per brand)."""
        path = self._encodings_path(brand_id)
        if not path.exists():
            return []
        try:
            data: list[tuple[str, np.ndarray]] = pickle.loads(path.read_bytes())
            log.debug(f"[FaceDatabase] Loaded {len(data)} encoding(s) for brand {brand_id}")
            return data
        except Exception as e:
            log.warning(f"[FaceDatabase] Could not read encodings for brand {brand_id}: {e}")
            return []

    def _save_encodings(self, brand_id: int, encodings: list[tuple[str, np.ndarray]]):
        """Persist encodings list to disk."""
        path = self._encodings_path(brand_id)
        path.write_bytes(pickle.dumps(encodings))

    def _get_brand_encodings(self, brand_id: int) -> list[tuple[str, np.ndarray]]:
        """Return cached encodings for a brand, loading from disk on first access."""
        if brand_id not in self._cache:
            self._cache[brand_id] = self._load_encodings(brand_id)
        return self._cache[brand_id]

    # ── Embedding generation ───────────────────────────────────────────────────

    def get_embedding(self, face_img_bgr: np.ndarray) -> Optional[np.ndarray]:
        """Generate a 128-dim L2-normalised embedding for a face crop.

        Tries face_recognition first; falls back to DeepFace/Facenet.
        Returns None if no backend is available or the image is too small.
        """
        if face_img_bgr is None or face_img_bgr.size == 0:
            return None

        # face_recognition expects RGB
        if _BACKEND == "face_recognition":
            rgb = cv2.cvtColor(face_img_bgr, cv2.COLOR_BGR2RGB)
            encs = _fr.face_encodings(rgb)
            if not encs:
                return None
            return _l2_normalize(np.array(encs[0]))

        # DeepFace expects a file path or BGR ndarray
        if _BACKEND == "deepface":
            try:
                # Write temp file — DeepFace's in-memory path is model-dependent
                _TMP_FACE_PATH.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(_TMP_FACE_PATH), face_img_bgr)
                result = _df.represent(
                    img_path=str(_TMP_FACE_PATH),
                    model_name="Facenet",
                    enforce_detection=False,
                )
                vec = np.array(result[0]["embedding"])
                return _l2_normalize(vec)
            except Exception as e:
                log.debug(f"[FaceDatabase] DeepFace embedding failed: {e}")
                return None

        return None     # no backend

    # ── Deduplication ──────────────────────────────────────────────────────────

    def is_duplicate(
        self,
        embedding: np.ndarray,
        brand_id: int,
    ) -> tuple[bool, Optional[str]]:
        """Check whether *embedding* matches an already-stored face for *brand_id*.

        Returns:
            (True, face_id)   – duplicate, matched face_id
            (False, None)     – new unique person
        """
        encodings = self._get_brand_encodings(brand_id)
        if not encodings:
            return False, None

        distances = [
            (_euclidean(embedding, enc), fid)
            for fid, enc in encodings
        ]
        min_dist, matched_fid = min(distances, key=lambda t: t[0])

        if min_dist < self._threshold:
            log.debug(
                f"[FaceDatabase] Duplicate detected for brand {brand_id} "
                f"(dist={min_dist:.3f}, match={matched_fid})"
            )
            return True, matched_fid

        return False, None

    # ── Storage ────────────────────────────────────────────────────────────────

    def add_face(
        self,
        face_img_bgr: np.ndarray,
        embedding: np.ndarray,
        brand_id: int,
    ) -> str:
        """Save a new face image and its embedding for *brand_id*.

        Assigns a sequential face ID (face_0001, face_0002, …) within the
        brand's folder.

        Returns:
            The assigned face_id string (e.g. ``"face_0003"``).
        """
        with self._lock:
            encodings = self._get_brand_encodings(brand_id)
            face_id   = f"face_{len(encodings) + 1:04d}"

            # Save image
            img_path = self._brand_dir(brand_id) / f"{face_id}.jpg"
            cv2.imwrite(str(img_path), face_img_bgr)

            # Update in-memory cache and persist to disk
            encodings.append((face_id, embedding))
            self._save_encodings(brand_id, encodings)

            log.info(
                f"[FaceDatabase] New face saved: brand={brand_id} "
                f"id={face_id} path={img_path}"
            )
            return face_id

    # ── Combined detect+dedup ──────────────────────────────────────────────────

    def process_face(
        self,
        face_img_bgr: np.ndarray,
        brand_id: int,
    ) -> tuple[bool, str, Optional[np.ndarray]]:
        """Full pipeline: embed → deduplicate → store if new.

        Returns:
            (is_new, face_id, embedding)

            is_new   – True if this is a newly registered unique person
            face_id  – ID of the stored face (new or matched existing)
            embedding – the computed embedding (None if backend unavailable)
        """
        embedding = self.get_embedding(face_img_bgr)

        if embedding is None:
            # No embedding backend: every face counted as new (fallback mode)
            with self._lock:
                encodings = self._get_brand_encodings(brand_id)
                face_id = f"face_{len(encodings) + 1:04d}"
                img_path = self._brand_dir(brand_id) / f"{face_id}.jpg"
                cv2.imwrite(str(img_path), face_img_bgr)
                # Store a zero-vector placeholder so face_id is registered
                placeholder = np.zeros(128, dtype=np.float32)
                encodings.append((face_id, placeholder))
                self._save_encodings(brand_id, encodings)
            return True, face_id, None

        is_dup, matched_id = self.is_duplicate(embedding, brand_id)

        if is_dup:
            return False, matched_id, embedding

        # New unique person — persist
        face_id = self.add_face(face_img_bgr, embedding, brand_id)
        return True, face_id, embedding

    # ── Introspection ──────────────────────────────────────────────────────────

    def face_count(self, brand_id: int) -> int:
        """Return number of unique faces stored for *brand_id*."""
        return len(self._get_brand_encodings(brand_id))

    def all_face_ids(self, brand_id: int) -> list[str]:
        """Return list of all stored face IDs for *brand_id*."""
        return [fid for fid, _ in self._get_brand_encodings(brand_id)]
