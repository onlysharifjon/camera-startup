"""
FaceService
-----------
Yuz aniqlash va tanish xizmati.

Detection backend (ustuvor tartibda):
  1. OpenCV DNN — SSD ResNet-10 (WIDER FACE dataset, ~97 % AP)
     models/deploy.prototxt + models/res10_300x300_ssd_iter_140000.caffemodel
  2. Haar Cascade frontal alt2 + profile  (fallback)

Validation pipeline (har ikkala backendda ham):
  - Confidence threshold (≥ 0.65)
  - Minimum face size (50×50 px)
  - Aspect ratio check (0.5 – 1.8)
  - Skin-tone heuristic (HSV mask ≥ 12 %)
  - Eye presence check (Haar fallback uchun)
  - Non-Maximum Suppression (IoU > 0.4)

Recognition:
  DeepFace + Facenet (128-dim embedding, Euclidean distance < 10.0)
"""

import logging
import os
import pickle
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from backend.core.config import settings

log = logging.getLogger(__name__)

# ── Model paths ────────────────────────────────────────────────────────────────
_HERE       = Path(__file__).parent
_MODELS_DIR = _HERE.parent.parent / "face_system" / "models"
_PROTO      = _MODELS_DIR / "deploy.prototxt"
_CAFFE      = _MODELS_DIR / "res10_300x300_ssd_iter_140000.caffemodel"

# ── Cascade paths ──────────────────────────────────────────────────────────────
_CC_DIR     = cv2.data.haarcascades
_FRONTAL    = _CC_DIR + "haarcascade_frontalface_alt2.xml"
_PROFILE    = _CC_DIR + "haarcascade_profileface.xml"
_EYE        = _CC_DIR + "haarcascade_eye_tree_eyeglasses.xml"

# ── Skin-tone HSV ranges ───────────────────────────────────────────────────────
_SKIN_L1 = np.array([0,   20,  70],  dtype=np.uint8)
_SKIN_U1 = np.array([25,  170, 255], dtype=np.uint8)
_SKIN_L2 = np.array([175, 20,  70],  dtype=np.uint8)
_SKIN_U2 = np.array([180, 170, 255], dtype=np.uint8)

# ── DeepFace (recognition only) ────────────────────────────────────────────────
try:
    from deepface import DeepFace
    DF_AVAILABLE = True
    log.info("[Face] DeepFace loaded")
except Exception as e:
    DeepFace      = None
    DF_AVAILABLE  = False
    log.warning(f"[Face] DeepFace unavailable: {e}")


# ── NMS helper ─────────────────────────────────────────────────────────────────

def _nms(boxes, scores, iou_thresh=0.4):
    if not boxes:
        return []
    xs = np.array([b[0] for b in boxes], dtype=np.float32)
    ys = np.array([b[1] for b in boxes], dtype=np.float32)
    ws = np.array([b[2] for b in boxes], dtype=np.float32)
    hs = np.array([b[3] for b in boxes], dtype=np.float32)
    sc = np.array(scores, dtype=np.float32)
    x2, y2 = xs + ws, ys + hs
    areas   = ws * hs
    order   = sc.argsort()[::-1]
    keep    = []
    while order.size:
        i = order[0]; keep.append(int(i))
        if order.size == 1:
            break
        ix1 = np.maximum(xs[i], xs[order[1:]])
        iy1 = np.maximum(ys[i], ys[order[1:]])
        ix2 = np.minimum(x2[i], x2[order[1:]])
        iy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0.0, ix2-ix1) * np.maximum(0.0, iy2-iy1)
        iou   = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)
        order = order[np.where(iou <= iou_thresh)[0] + 1]
    return keep


# ── Main service ────────────────────────────────────────────────────────────────

class FaceService:

    # Detection hyper-parameters
    CONF_THRESH  = 0.65    # DNN minimum confidence
    MIN_SIZE     = 50      # minimum face pixel dimension
    SKIN_RATIO   = 0.12    # minimum skin-pixel fraction
    NMS_IOU      = 0.40    # NMS overlap threshold

    def __init__(self):
        self._lock = threading.Lock()

        # ── Load detection backends ────────────────────────────────────────────
        self._dnn     = self._load_dnn()
        self._frontal = self._load_cc(_FRONTAL, "frontal alt2")
        self._profile = self._load_cc(_PROFILE, "profile")
        self._eye_cc  = self._load_cc(_EYE,     "eye+glasses")

        backend = "DNN SSD ResNet-10" if self._dnn else "Haar Cascade"
        log.info(f"[Face] Detection backend: {backend}")

        # ── Load face embeddings for recognition ───────────────────────────────
        self._embeddings: dict[str, np.ndarray] = {}
        self._load_embeddings()

    # ── Loaders ────────────────────────────────────────────────────────────────

    @staticmethod
    def _load_dnn():
        if not _PROTO.exists() or not _CAFFE.exists():
            log.warning("[Face] DNN model files not found — using Haar Cascade fallback")
            return None
        try:
            net = cv2.dnn.readNetFromCaffe(str(_PROTO), str(_CAFFE))
            net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            log.info("[Face] DNN SSD ResNet-10 loaded OK")
            return net
        except Exception as e:
            log.warning(f"[Face] DNN load error: {e}")
            return None

    @staticmethod
    def _load_cc(path, name):
        cc = cv2.CascadeClassifier(path)
        if cc.empty():
            log.warning(f"[Face] Cascade '{name}' not found: {path}")
            return None
        return cc

    def _load_embeddings(self):
        self._embeddings.clear()
        d = settings.FACES_DIR
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob("*.pkl"):
            try:
                data = pickle.loads(f.read_bytes())
                self._embeddings[data["employee_id"]] = np.array(data["embedding"])
            except Exception as e:
                log.warning(f"[Face] Cannot load {f.name}: {e}")
        log.info(f"[Face] {len(self._embeddings)} employee(s) enrolled")

    def reload(self):
        self._load_embeddings()

    # ── Enrollment ─────────────────────────────────────────────────────────────

    def enroll_employee(self, employee_id: str, image_bytes: bytes) -> bool:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return False

        img_path = settings.FACES_DIR / f"{employee_id}.jpg"
        cv2.imwrite(str(img_path), img)

        if not DF_AVAILABLE:
            pkl_path = settings.FACES_DIR / f"{employee_id}.pkl"
            pkl_path.write_bytes(pickle.dumps({"employee_id": employee_id, "embedding": []}))
            self.reload()
            return True

        try:
            result    = DeepFace.represent(str(img_path), model_name="Facenet", enforce_detection=True)
            embedding = np.array(result[0]["embedding"])
            pkl_path  = settings.FACES_DIR / f"{employee_id}.pkl"
            pkl_path.write_bytes(pickle.dumps({"employee_id": employee_id, "embedding": embedding.tolist()}))
            self.reload()
            log.info(f"[Face] Enrolled: {employee_id}")
            return True
        except Exception as e:
            log.error(f"[Face] Enroll failed for {employee_id}: {e}")
            img_path.unlink(missing_ok=True)
            return False

    # ── Public detection + recognition ────────────────────────────────────────

    def detect_and_recognize(self, frame_bgr: np.ndarray) -> list[dict]:
        """Detect faces and match to enrolled employees."""
        faces = self._detect(frame_bgr)
        for face in faces:
            face["employee_id"] = self._recognize(frame_bgr, face["box"])
        return faces

    # ── Detection ──────────────────────────────────────────────────────────────

    def _detect(self, frame_bgr: np.ndarray) -> list[dict]:
        h, w = frame_bgr.shape[:2]
        if h == 0 or w == 0:
            return []

        raw = self._raw_dnn(frame_bgr) if self._dnn else self._raw_haar(frame_bgr)
        if not raw:
            return []

        # Validation + NMS
        valid_boxes, valid_scores = [], []
        for (bx, by, bw, bh), conf in raw:
            if self._passes_validation(frame_bgr, bx, by, bw, bh):
                valid_boxes.append((bx, by, bw, bh))
                valid_scores.append(conf)

        keep = _nms(valid_boxes, valid_scores, self.NMS_IOU)
        return [
            {
                "box": {
                    "x": valid_boxes[i][0],
                    "y": valid_boxes[i][1],
                    "w": valid_boxes[i][2],
                    "h": valid_boxes[i][3],
                },
                "employee_id": None,
                "confidence":  round(valid_scores[i], 3),
            }
            for i in keep
        ]

    def _raw_dnn(self, frame_bgr):
        h, w = frame_bgr.shape[:2]
        blob = cv2.dnn.blobFromImage(
            cv2.resize(frame_bgr, (300, 300)), 1.0, (300, 300),
            (104.0, 177.0, 123.0), False, False,
        )
        self._dnn.setInput(blob)
        dets = self._dnn.forward()
        results = []
        for i in range(dets.shape[2]):
            conf = float(dets[0, 0, i, 2])
            if conf < self.CONF_THRESH:
                continue
            x1 = max(0, int(dets[0, 0, i, 3] * w))
            y1 = max(0, int(dets[0, 0, i, 4] * h))
            x2 = min(w, int(dets[0, 0, i, 5] * w))
            y2 = min(h, int(dets[0, 0, i, 6] * h))
            bw, bh = x2 - x1, y2 - y1
            if bw > 0 and bh > 0:
                results.append(((x1, y1, bw, bh), conf))
        return results

    def _raw_haar(self, frame_bgr):
        gray  = cv2.equalizeHist(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY))
        ms    = (self.MIN_SIZE, self.MIN_SIZE)
        results = []
        if self._frontal:
            for (x,y,w,h) in (self._frontal.detectMultiScale(gray,1.1,6,minSize=ms) if True else []):
                results.append(((int(x),int(y),int(w),int(h)), 0.80))
        if self._profile:
            for (x,y,w,h) in (self._profile.detectMultiScale(gray,1.1,5,minSize=ms) if True else []):
                results.append(((int(x),int(y),int(w),int(h)), 0.70))
            fw = frame_bgr.shape[1]
            gf = cv2.flip(gray, 1)
            for (x,y,w,h) in (self._profile.detectMultiScale(gf,1.1,5,minSize=ms) if True else []):
                results.append(((fw-int(x)-int(w), int(y), int(w), int(h)), 0.70))
        return results

    def _passes_validation(self, frame_bgr, x, y, w, h) -> bool:
        fh, fw = frame_bgr.shape[:2]
        # Size
        if w < self.MIN_SIZE or h < self.MIN_SIZE:
            return False
        # Aspect ratio
        if not (0.5 <= w / max(h, 1) <= 1.8):
            return False
        # Crop
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(fw, x+w), min(fh, y+h)
        region  = frame_bgr[y1:y2, x1:x2]
        if region.size == 0:
            return False
        # Skin tone
        hsv   = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
        mask  = cv2.bitwise_or(
            cv2.inRange(hsv, _SKIN_L1, _SKIN_U1),
            cv2.inRange(hsv, _SKIN_L2, _SKIN_U2),
        )
        if float(np.count_nonzero(mask)) / mask.size < self.SKIN_RATIO:
            return False
        # Eye check (Haar only)
        if not self._dnn and self._eye_cc:
            upper = region[:int(h * 0.6), :]
            if upper.size > 0:
                g = cv2.equalizeHist(cv2.cvtColor(upper, cv2.COLOR_BGR2GRAY))
                if len(self._eye_cc.detectMultiScale(g, 1.1, 5, minSize=(15,15))) == 0:
                    return False
        return True

    # ── Recognition ────────────────────────────────────────────────────────────

    def _recognize(self, frame_bgr: np.ndarray, box: dict) -> Optional[str]:
        if not self._embeddings or not DF_AVAILABLE:
            return None
        try:
            x, y, w, h = box["x"], box["y"], box["w"], box["h"]
            pad   = int(min(w, h) * 0.1)
            fh, fw = frame_bgr.shape[:2]
            crop  = frame_bgr[max(0,y-pad):min(fh,y+h+pad), max(0,x-pad):min(fw,x+w+pad)]
            if crop.size == 0:
                return None

            tmp = str(settings.FACES_DIR / "_tmp_recog.jpg")
            cv2.imwrite(tmp, crop)

            res       = DeepFace.represent(tmp, model_name="Facenet", enforce_detection=False)
            query     = np.array(res[0]["embedding"])
            best_id   = None
            best_dist = float("inf")

            for emp_id, known in self._embeddings.items():
                if len(known) == 0:
                    continue
                d = float(np.linalg.norm(query - known))
                if d < best_dist:
                    best_dist, best_id = d, emp_id

            # Facenet euclidean threshold ≈ 10
            return best_id if best_dist < 10.0 else None
        except Exception:
            return None

    # ── Snapshot ───────────────────────────────────────────────────────────────

    def save_snapshot(self, frame_bgr: np.ndarray, camera_id: str, suffix: str = "") -> str:
        today   = datetime.now().strftime("%Y-%m-%d")
        ts      = datetime.now().strftime("%H-%M-%S-%f")[:15]
        out_dir = settings.CAPTURES_DIR / camera_id / today
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{ts}{suffix}.jpg"
        cv2.imwrite(str(path), frame_bgr)
        return str(path)


face_service = FaceService()
