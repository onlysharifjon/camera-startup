"""
FaceDetector
============
High-accuracy human face detector with a multi-layer validation pipeline.

Detection backends (tried in priority order):
  1. OpenCV DNN – SSD ResNet-10 trained on WIDER FACE (~97 % AP)
     Model: res10_300x300_ssd_iter_140000.caffemodel + deploy.prototxt
     • Real deep-learning detector — handles pose variation, occlusion, lighting
     • Per-face confidence score → configurable threshold
  2. Multi-cascade Haar (frontal + profile)
     • haarcascade_frontalface_alt2  (fewer false positives than _default)
     • haarcascade_profileface       (side-view faces)

Validation pipeline (applied to every candidate regardless of backend):
  ┌─────────────────────────────────────────────────────┐
  │  1. Confidence threshold     (DNN ≥ 0.65)           │
  │  2. Minimum face size        (default 50×50 px)     │
  │  3. Aspect ratio             (w/h must be 0.5–1.8)  │
  │  4. Skin-tone heuristic      (HSV colour in face)   │
  │  5. Eye presence             (Haar fallback only)   │
  │  6. Non-Maximum Suppression  (deduplicate boxes)    │
  └─────────────────────────────────────────────────────┘

Why each step:
  • Confidence threshold  – DNN gives a probability; anything < 0.65 is noise.
  • Min size              – Tiny blobs are almost never real faces.
  • Aspect ratio          – Human faces are roughly square; tall slivers or wide
                           rectangles are background artifacts.
  • Skin tone             – At least 15 % of the face crop must contain
                           skin-like pixels (HSV colour range).  This alone
                           eliminates most textured-wall false positives.
  • Eye presence          – For the Haar fallback, run a lightweight eye
                           detector inside the upper half of each candidate.
                           No eyes → not a real face.
  • NMS                   – When multiple boxes overlap (IoU > 0.4), keep only
                           the highest-confidence one.
"""

import logging
import os
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
_HERE        = Path(__file__).parent
_MODELS_DIR  = _HERE / "models"
_PROTO_PATH  = _MODELS_DIR / "deploy.prototxt"
_MODEL_PATH  = _MODELS_DIR / "res10_300x300_ssd_iter_140000.caffemodel"

# ── Cascade paths ─────────────────────────────────────────────────────────────
_CASCADE_DIR     = cv2.data.haarcascades
_FRONTAL_XML     = _CASCADE_DIR + "haarcascade_frontalface_alt2.xml"
_PROFILE_XML     = _CASCADE_DIR + "haarcascade_profileface.xml"
_EYE_XML         = _CASCADE_DIR + "haarcascade_eye.xml"
_EYE_GLASSES_XML = _CASCADE_DIR + "haarcascade_eye_tree_eyeglasses.xml"

# ── Skin-tone HSV range (works for most ethnicities) ─────────────────────────
# Lower: hue 0-25, sat 20-170, val 70-255
# Upper: hue 175-180 catches the red wrap-around
_SKIN_LOWER1 = np.array([0,   20,  70],  dtype=np.uint8)
_SKIN_UPPER1 = np.array([25,  170, 255], dtype=np.uint8)
_SKIN_LOWER2 = np.array([175, 20,  70],  dtype=np.uint8)
_SKIN_UPPER2 = np.array([180, 170, 255], dtype=np.uint8)


# ── Helper: Non-Maximum Suppression ───────────────────────────────────────────

def _nms(
    boxes: list[tuple[int, int, int, int]],
    scores: list[float],
    iou_threshold: float = 0.4,
) -> list[int]:
    """Return indices of boxes to keep after NMS."""
    if not boxes:
        return []

    xs = np.array([b[0] for b in boxes], dtype=np.float32)
    ys = np.array([b[1] for b in boxes], dtype=np.float32)
    ws = np.array([b[2] for b in boxes], dtype=np.float32)
    hs = np.array([b[3] for b in boxes], dtype=np.float32)
    sc = np.array(scores, dtype=np.float32)

    x2 = xs + ws
    y2 = ys + hs
    areas = ws * hs

    order = sc.argsort()[::-1]
    keep  = []

    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        if order.size == 1:
            break

        ix1 = np.maximum(xs[i], xs[order[1:]])
        iy1 = np.maximum(ys[i], ys[order[1:]])
        ix2 = np.minimum(x2[i], x2[order[1:]])
        iy2 = np.minimum(y2[i], y2[order[1:]])

        inter_w = np.maximum(0.0, ix2 - ix1)
        inter_h = np.maximum(0.0, iy2 - iy1)
        inter   = inter_w * inter_h
        iou     = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)

        order = order[np.where(iou <= iou_threshold)[0] + 1]

    return keep


# ── Main class ─────────────────────────────────────────────────────────────────

class FaceDetector:
    """Multi-backend face detector with validation pipeline.

    Args:
        confidence_threshold: DNN minimum detection confidence (0-1). Default 0.65.
        min_face_size:        Minimum face pixel dimension. Default 50.
        nms_iou_threshold:    IoU overlap threshold for NMS. Default 0.4.
        skin_min_ratio:       Minimum fraction of face crop that must be
                              skin-coloured to pass validation. Default 0.12.
        require_eyes:         For Haar fallback – reject candidates with no
                              detected eyes. Default True.
        use_dnn:              Force DNN (True), force Haar (False), or auto (None).
    """

    def __init__(
        self,
        confidence_threshold: float = 0.65,
        min_face_size:        int   = 50,
        nms_iou_threshold:    float = 0.4,
        skin_min_ratio:       float = 0.12,
        require_eyes:         bool  = True,
        use_dnn:              Optional[bool] = None,
    ):
        self._conf_thresh  = confidence_threshold
        self._min_size     = min_face_size
        self._nms_iou      = nms_iou_threshold
        self._skin_ratio   = skin_min_ratio
        self._require_eyes = require_eyes

        self._dnn_net      = None
        self._frontal_cc   = None
        self._profile_cc   = None
        self._eye_cc       = None

        # ── Try DNN ────────────────────────────────────────────────────────────
        if use_dnn is not False:
            self._dnn_net = self._load_dnn()

        # ── Always load Haar cascades (used as fallback or for eye validation) ─
        self._frontal_cc = self._load_cascade(_FRONTAL_XML, "frontal alt2")
        self._profile_cc = self._load_cascade(_PROFILE_XML, "profile")
        self._eye_cc     = (
            self._load_cascade(_EYE_GLASSES_XML, "eye+glasses")
            or self._load_cascade(_EYE_XML, "eye")
        )

        backend = "DNN (SSD ResNet-10)" if self._dnn_net else "Haar Cascade"
        log.info(f"[FaceDetector] Backend: {backend}  min_size={min_face_size}px  conf≥{confidence_threshold}")

    # ── Loader helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _load_dnn():
        if not _PROTO_PATH.exists() or not _MODEL_PATH.exists():
            log.warning(
                "[FaceDetector] DNN model files missing – "
                f"expected {_PROTO_PATH} and {_MODEL_PATH}"
            )
            return None
        try:
            net = cv2.dnn.readNetFromCaffe(str(_PROTO_PATH), str(_MODEL_PATH))
            # Use CPU (backend = DEFAULT, target = CPU)
            net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            log.info("[FaceDetector] DNN SSD ResNet-10 loaded OK")
            return net
        except Exception as e:
            log.warning(f"[FaceDetector] DNN load failed: {e}")
            return None

    @staticmethod
    def _load_cascade(path: str, name: str):
        cc = cv2.CascadeClassifier(path)
        if cc.empty():
            log.warning(f"[FaceDetector] Cascade '{name}' not found at {path}")
            return None
        return cc

    # ── Public API ─────────────────────────────────────────────────────────────

    def detect(self, frame_bgr: np.ndarray) -> list[dict]:
        """Detect human faces in a BGR frame.

        Returns a list of dicts:
            box        – {"x": int, "y": int, "w": int, "h": int}
            confidence – float  (DNN probability or 0.80 for Haar)
        """
        h, w = frame_bgr.shape[:2]
        if h == 0 or w == 0:
            return []

        # ── Step 1: raw candidates from primary backend ────────────────────────
        if self._dnn_net:
            raw = self._detect_dnn(frame_bgr)
        else:
            raw = self._detect_haar(frame_bgr)

        if not raw:
            return []

        # ── Step 2: apply validation pipeline ────────────────────────────────
        validated = []
        for box, conf in raw:
            if not self._validate(frame_bgr, box, conf):
                continue
            validated.append((box, conf))

        if not validated:
            return []

        # ── Step 3: Non-Maximum Suppression ──────────────────────────────────
        boxes  = [b for b, _ in validated]
        scores = [s for _, s in validated]
        keep   = _nms(boxes, scores, self._nms_iou)

        return [
            {
                "box": {
                    "x": boxes[i][0],
                    "y": boxes[i][1],
                    "w": boxes[i][2],
                    "h": boxes[i][3],
                },
                "confidence": round(scores[i], 3),
            }
            for i in keep
        ]

    def crop(
        self,
        frame_bgr: np.ndarray,
        box: dict,
        padding: float = 0.15,
    ) -> Optional[np.ndarray]:
        """Crop a face region with proportional padding.

        Returns BGR crop, or None if crop area is empty.
        """
        x, y, w, h = box["x"], box["y"], box["w"], box["h"]
        pad_x = int(w * padding)
        pad_y = int(h * padding)
        fh, fw = frame_bgr.shape[:2]
        x1 = max(0, x - pad_x)
        y1 = max(0, y - pad_y)
        x2 = min(fw, x + w + pad_x)
        y2 = min(fh, y + h + pad_y)
        crop = frame_bgr[y1:y2, x1:x2]
        return crop if crop.size > 0 else None

    def annotate(self, frame_bgr: np.ndarray, faces: list[dict]) -> np.ndarray:
        """Draw face boxes and people-count overlay. Returns a copy."""
        out   = frame_bgr.copy()
        count = len(faces)

        for face in faces:
            b = face["box"]
            x, y, w, h = b["x"], b["y"], b["w"], b["h"]
            label    = face.get("label", "Unknown")
            is_known = face.get("employee_id") or (label not in ("Unknown", ""))

            color = (0, 220, 80) if is_known else (0, 165, 255)
            cv2.rectangle(out, (x, y), (x + w, y + h), color, 2)

            # Confidence badge
            conf_txt = f"{face.get('confidence', 0):.2f}"
            (cw, ch), _ = cv2.getTextSize(conf_txt, cv2.FONT_HERSHEY_SIMPLEX, 0.38, 1)
            cv2.rectangle(out, (x, y + h), (x + cw + 4, y + h + ch + 4), color, -1)
            cv2.putText(out, conf_txt, (x + 2, y + h + ch + 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 0, 0), 1, cv2.LINE_AA)

            # Label above box
            (lw, lh), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
            cv2.rectangle(out, (x, y - lh - 8), (x + lw + 6, y), color, -1)
            cv2.putText(out, label, (x + 3, y - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA)

        # People count — top-left corner
        ct = f"People: {count}"
        (tw, th), _ = cv2.getTextSize(ct, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
        cv2.rectangle(out, (8, 8), (tw + 18, th + 18), (0, 0, 0), -1)
        cv2.putText(out, ct, (14, th + 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 220, 80), 2, cv2.LINE_AA)

        return out

    # ── Backend 1: DNN ────────────────────────────────────────────────────────

    def _detect_dnn(
        self, frame_bgr: np.ndarray
    ) -> list[tuple[tuple[int,int,int,int], float]]:
        """Run SSD ResNet-10 detector.  Returns [(x,y,w,h), confidence] pairs."""
        h, w = frame_bgr.shape[:2]

        # Pre-process: resize to 300×300, mean-subtract
        blob = cv2.dnn.blobFromImage(
            cv2.resize(frame_bgr, (300, 300)),
            scalefactor = 1.0,
            size        = (300, 300),
            mean        = (104.0, 177.0, 123.0),
            swapRB      = False,
            crop        = False,
        )
        self._dnn_net.setInput(blob)
        detections = self._dnn_net.forward()   # shape: (1,1,N,7)

        results = []
        for i in range(detections.shape[2]):
            conf = float(detections[0, 0, i, 2])
            if conf < self._conf_thresh:
                continue

            x1 = max(0, int(detections[0, 0, i, 3] * w))
            y1 = max(0, int(detections[0, 0, i, 4] * h))
            x2 = min(w, int(detections[0, 0, i, 5] * w))
            y2 = min(h, int(detections[0, 0, i, 6] * h))

            bw = x2 - x1
            bh = y2 - y1
            if bw <= 0 or bh <= 0:
                continue

            results.append(((x1, y1, bw, bh), conf))

        return results

    # ── Backend 2: Haar Cascade (frontal + profile) ────────────────────────────

    def _detect_haar(
        self, frame_bgr: np.ndarray
    ) -> list[tuple[tuple[int,int,int,int], float]]:
        """Run frontal and profile Haar cascades. Returns [(x,y,w,h), conf]."""
        gray  = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        gray  = cv2.equalizeHist(gray)
        ms    = (self._min_size, self._min_size)
        results: list[tuple[tuple[int,int,int,int], float]] = []

        # Frontal faces
        if self._frontal_cc:
            dets = self._frontal_cc.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=6,
                minSize=ms, flags=cv2.CASCADE_SCALE_IMAGE,
            )
            for (x, y, w, h) in (dets if len(dets) else []):
                results.append(((int(x), int(y), int(w), int(h)), 0.80))

        # Profile faces (mirrored too for right-facing profiles)
        if self._profile_cc:
            dets = self._profile_cc.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=5,
                minSize=ms, flags=cv2.CASCADE_SCALE_IMAGE,
            )
            for (x, y, w, h) in (dets if len(dets) else []):
                results.append(((int(x), int(y), int(w), int(h)), 0.70))

            # Mirror the frame and run again to catch the other profile
            gray_flip = cv2.flip(gray, 1)
            fw = frame_bgr.shape[1]
            dets_flip = self._profile_cc.detectMultiScale(
                gray_flip, scaleFactor=1.1, minNeighbors=5,
                minSize=ms, flags=cv2.CASCADE_SCALE_IMAGE,
            )
            for (x, y, w, h) in (dets_flip if len(dets_flip) else []):
                # Convert flipped coords back to original
                x_orig = fw - x - w
                results.append(((int(x_orig), int(y), int(w), int(h)), 0.70))

        return results

    # ── Validation pipeline ────────────────────────────────────────────────────

    def _validate(
        self,
        frame_bgr: np.ndarray,
        box: tuple[int, int, int, int],
        confidence: float,
    ) -> bool:
        """Return True if *box* passes all heuristic checks."""
        x, y, w, h = box
        fh, fw = frame_bgr.shape[:2]

        # 1. Minimum size
        if w < self._min_size or h < self._min_size:
            return False

        # 2. Aspect ratio — human faces are roughly square (w/h ≈ 0.6–1.5)
        ratio = w / max(h, 1)
        if not (0.5 <= ratio <= 1.8):
            log.debug(f"[FaceDetector] Rejected (aspect ratio {ratio:.2f})")
            return False

        # 3. Box must be mostly inside the frame
        x2, y2 = x + w, y + h
        if x < 0 or y < 0 or x2 > fw or y2 > fh:
            # Clip to frame and check how much is visible
            visible_w = min(x2, fw) - max(x, 0)
            visible_h = min(y2, fh) - max(y, 0)
            if visible_w * visible_h < 0.7 * w * h:
                return False

        # 4. Skin-tone heuristic
        x1c = max(0, x)
        y1c = max(0, y)
        x2c = min(fw, x + w)
        y2c = min(fh, y + h)
        face_region = frame_bgr[y1c:y2c, x1c:x2c]
        if face_region.size == 0:
            return False
        if not self._has_skin_tone(face_region):
            log.debug("[FaceDetector] Rejected (no skin tone)")
            return False

        # 5. Eye presence check (Haar fallback only — DNN already knows it's a face)
        if self._require_eyes and not self._dnn_net and self._eye_cc:
            if not self._has_eyes(face_region):
                log.debug("[FaceDetector] Rejected (no eyes detected)")
                return False

        return True

    def _has_skin_tone(self, face_bgr: np.ndarray) -> bool:
        """Return True if ≥ skin_min_ratio of pixels are skin-coloured."""
        hsv   = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2HSV)
        mask1 = cv2.inRange(hsv, _SKIN_LOWER1, _SKIN_UPPER1)
        mask2 = cv2.inRange(hsv, _SKIN_LOWER2, _SKIN_UPPER2)
        mask  = cv2.bitwise_or(mask1, mask2)

        skin_ratio = float(np.count_nonzero(mask)) / mask.size
        return skin_ratio >= self._skin_ratio

    def _has_eyes(self, face_bgr: np.ndarray) -> bool:
        """Return True if at least one eye is detected in the upper face crop."""
        h = face_bgr.shape[0]
        # Eyes are in the upper 60 % of the face
        upper = face_bgr[: int(h * 0.6), :]
        if upper.size == 0:
            return False
        gray_upper = cv2.cvtColor(upper, cv2.COLOR_BGR2GRAY)
        gray_upper = cv2.equalizeHist(gray_upper)
        eyes = self._eye_cc.detectMultiScale(
            gray_upper,
            scaleFactor  = 1.1,
            minNeighbors = 5,
            minSize      = (15, 15),
        )
        return len(eyes) >= 1
