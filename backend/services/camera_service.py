"""
CameraService
-------------
RTSP va V380 kamera oqimlarini boshqaradi.
Har bir kamera uchun alohida thread ishlatadi.
Yuz aniqlanganda WebSocket broadcast qiladi.
"""
import asyncio, logging, threading, time, io
from pathlib import Path
from typing import Callable
from datetime import datetime

import cv2
import numpy as np

from backend.core.config       import settings
from backend.services.face_service import face_service

log = logging.getLogger(__name__)


class CameraStream:
    """Bitta kamera uchun stream thread."""

    def __init__(self, camera_id: str, url: str, on_detection: Callable,
                 loop: asyncio.AbstractEventLoop | None = None):
        self.camera_id    = camera_id
        self.url          = url
        self.on_detection = on_detection   # async callback
        self._loop        = loop
        self.active       = False
        self._thread      = None
        self._latest_jpeg: bytes | None = None
        self._latest_faces: list        = []        # so'nggi aniqlangan yuzlar
        self._latest_annotated: bytes | None = None # yuz box'li frame
        self._lock        = threading.Lock()

    def start(self):
        if self.active:
            return
        self.active  = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log.info(f"[CAM] Started {self.camera_id}")

    def stop(self):
        self.active = False
        log.info(f"[CAM] Stopped {self.camera_id}")

    def latest_frame(self) -> bytes | None:
        with self._lock:
            return self._latest_jpeg

    def latest_annotated(self) -> bytes | None:
        with self._lock:
            return self._latest_annotated or self._latest_jpeg

    def latest_faces(self) -> list:
        with self._lock:
            return list(self._latest_faces)

    @staticmethod
    def _draw_faces(frame, faces: list):
        """Frame ustiga yuz box va odamlar soni chizish."""
        count = len(faces)
        for face in faces:
            b   = face["box"]
            x, y, w, h = b["x"], b["y"], b["w"], b["h"]
            emp = face.get("employee_id")
            # Box
            color = (0, 220, 80) if emp else (0, 180, 255)
            cv2.rectangle(frame, (x, y), (x+w, y+h), color, 2)
            # Label
            label = emp if emp else "Noma'lum"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
            cv2.rectangle(frame, (x, y-th-8), (x+tw+6, y), color, -1)
            cv2.putText(frame, label, (x+3, y-4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0,0,0), 1, cv2.LINE_AA)
        # People count top-left
        label2 = f"Odamlar: {count}"
        (tw2, th2), _ = cv2.getTextSize(label2, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
        cv2.rectangle(frame, (8, 8), (tw2+18, th2+18), (0,0,0), -1)
        cv2.putText(frame, label2, (14, th2+12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,220,80), 2, cv2.LINE_AA)
        return frame

    def _run(self):
        retry_delay = 5
        while self.active:
            cap = cv2.VideoCapture(self.url)
            if not cap.isOpened():
                log.warning(f"[CAM] Cannot open {self.url} – retry in {retry_delay}s")
                time.sleep(retry_delay)
                continue

            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            last_detect = 0

            while self.active:
                ret, frame = cap.read()
                if not ret:
                    log.warning(f"[CAM] Frame read failed – reconnecting")
                    break

                # JPEG encode
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ok:
                    with self._lock:
                        self._latest_jpeg = buf.tobytes()

                # Face detection (1 fps)
                now = time.time()
                if now - last_detect >= settings.FACE_DETECTION_INTERVAL:
                    last_detect = now
                    faces = face_service.detect_and_recognize(frame)

                    # Annotated frame (har doim — yuz bo'lsa box, bo'lmasa sof frame)
                    annotated = self._draw_faces(frame.copy(), faces)
                    ok2, abuf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 75])
                    with self._lock:
                        self._latest_faces    = faces
                        self._latest_annotated = abuf.tobytes() if ok2 else None

                    if faces:
                        snapshot = face_service.save_snapshot(frame, self.camera_id)
                        loop = self._loop
                        if loop and loop.is_running():
                            asyncio.run_coroutine_threadsafe(
                                self.on_detection(self.camera_id, faces, snapshot, buf.tobytes()),
                                loop,
                            )

            cap.release()
            if self.active:
                log.info(f"[CAM] Reconnecting {self.camera_id} in {retry_delay}s")
                time.sleep(retry_delay)


class CameraService:
    def __init__(self):
        self._streams: dict[str, CameraStream] = {}
        self._on_detection_cb: Callable | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_detection_callback(self, cb: Callable, loop: asyncio.AbstractEventLoop):
        self._on_detection_cb = cb
        self._loop = loop

    async def _on_detection(self, camera_id: str, faces: list, snapshot: str, jpeg: bytes):
        if self._on_detection_cb:
            await self._on_detection_cb(camera_id, faces, snapshot, jpeg)

    def start_camera(self, camera_id: str, url: str):
        if camera_id in self._streams:
            self._streams[camera_id].stop()

        stream = CameraStream(camera_id, url, self._on_detection, self._loop)
        self._streams[camera_id] = stream
        stream.start()

    def stop_camera(self, camera_id: str):
        if camera_id in self._streams:
            self._streams[camera_id].stop()
            del self._streams[camera_id]

    def stop_all(self):
        for s in self._streams.values():
            s.stop()
        self._streams.clear()

    def get_latest_frame(self, camera_id: str) -> bytes | None:
        s = self._streams.get(camera_id)
        return s.latest_frame() if s else None

    def get_annotated_frame(self, camera_id: str) -> bytes | None:
        s = self._streams.get(camera_id)
        return s.latest_annotated() if s else None

    def get_latest_faces(self, camera_id: str) -> list:
        s = self._streams.get(camera_id)
        return s.latest_faces() if s else []

    def status(self) -> dict:
        return {cid: s.active for cid, s in self._streams.items()}

    # ── V380 screenshot watcher ────────────────────────────────────────────────

    def start_v380_watcher(self, camera_id: str = "v380-darvoza"):
        """V380 Screenshot papkasini kuzatadi."""
        t = threading.Thread(
            target=self._v380_watch_loop,
            args=(camera_id,),
            daemon=True,
        )
        t.start()
        log.info(f"[V380] Watcher started for {camera_id}")

    def _v380_watch_loop(self, camera_id: str):
        watch_dirs = [settings.V380_SCREENSHOT_DIR, settings.V380_RECORD_DIR]
        seen: set[str] = set()

        for d in watch_dirs:
            d.mkdir(parents=True, exist_ok=True)

        while True:
            for directory in watch_dirs:
                for f in sorted(directory.glob("*.jpg"))[-5:]:
                    key = str(f)
                    if key in seen:
                        continue
                    seen.add(key)
                    if len(seen) > 1000:
                        seen = set(list(seen)[-500:])

                    try:
                        frame = cv2.imread(str(f))
                        if frame is None:
                            continue
                        faces = face_service.detect_and_recognize(frame)
                        if not faces:
                            continue
                        snapshot = face_service.save_snapshot(frame, camera_id)
                        ok, buf  = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                        if ok and self._on_detection_cb:
                            asyncio.run_coroutine_threadsafe(
                                self._on_detection(camera_id, faces, snapshot, buf.tobytes()),
                                self._loop or asyncio.get_event_loop(),
                            )
                    except Exception as e:
                        log.warning(f"[V380] Frame error: {e}")

            time.sleep(1)


camera_service = CameraService()
