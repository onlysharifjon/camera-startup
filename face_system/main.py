"""
face_system/main.py
===================
Demo entry point for the multi-camera face detection system.

Run modes:
    python -m face_system.main                → webcam demo (device 0)
    python -m face_system.main --rtsp URL     → single RTSP stream
    python -m face_system.main --stats-only   → print stats every 5 s, no display

Controls (when display window is open):
    q  – quit
    s  – print current stats to console
"""

import argparse
import logging
import signal
import time

import cv2

from .camera_manager import CameraManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
)
log = logging.getLogger(__name__)


# ── Example configuration ──────────────────────────────────────────────────────

BRANDS: dict[int, str] = {
    101: "Brand_A",
    202: "Brand_B",
}

CAMERAS: list[dict] = [
    {
        "camera_id":   1,
        "camera_name": "Entrance Cam",
        "brand_id":    101,
        "source":      0,           # webcam device 0  (overridden by --rtsp)
    },
    # Uncomment to add more cameras:
    # {
    #     "camera_id":   2,
    #     "camera_name": "Checkout Cam",
    #     "brand_id":    101,
    #     "source":      "rtsp://admin:pass@192.168.1.101:554/stream",
    # },
    # {
    #     "camera_id":   3,
    #     "camera_name": "Hall Cam",
    #     "brand_id":    202,
    #     "source":      "rtsp://admin:pass@192.168.1.102:554/stream",
    # },
]


# ── Callback ───────────────────────────────────────────────────────────────────

def on_detection(camera_id: int, faces: list[dict]):
    """Called from a camera thread each time new faces are processed."""
    new_faces = [f for f in faces if f["is_new"]]
    if new_faces:
        print(
            f"  [cam {camera_id}] {len(new_faces)} new unique visitor(s) detected! "
            f"face_ids={[f['face_id'] for f in new_faces]}"
        )


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Multi-camera face detection demo")
    parser.add_argument("--rtsp",       type=str,  default=None,  help="RTSP URL override for camera 1")
    parser.add_argument("--stats-only", action="store_true",      help="Print stats only, no display window")
    parser.add_argument("--interval",   type=float, default=1.0,  help="Detection interval in seconds (default 1.0)")
    parser.add_argument("--threshold",  type=float, default=0.6,  help="Dedup distance threshold (default 0.6)")
    parser.add_argument("--db",         type=str,  default="faces_db", help="Face database root directory")
    args = parser.parse_args()

    # Apply RTSP override
    cameras = list(CAMERAS)
    if args.rtsp:
        cameras[0]["source"] = args.rtsp
        log.info(f"Using RTSP source: {args.rtsp}")

    mgr = CameraManager(
        cameras          = cameras,
        brands           = BRANDS,
        db_root          = args.db,
        dedup_threshold  = args.threshold,
        detect_interval  = args.interval,
        on_detection     = on_detection,
    )

    # Graceful shutdown on Ctrl-C
    def _shutdown(sig, frame):
        print("\nShutting down…")
        mgr.stop_all()

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    mgr.start_all()
    log.info("All cameras started. Press 'q' in the preview window or Ctrl-C to quit.")

    # ── Display loop ───────────────────────────────────────────────────────────
    if args.stats_only:
        try:
            while True:
                time.sleep(5)
                print(mgr.summary())
                for s in mgr.camera_stats():
                    print(
                        f"  cam {s['camera_id']:2d} ({s['camera_name']:20s}) "
                        f"events={s['total_detections']:4d}  new={s['unique_new']:4d}"
                    )
        except KeyboardInterrupt:
            pass
    else:
        # Show annotated frame from the first camera in a window
        first_cam_id = cameras[0]["camera_id"]
        try:
            while True:
                jpeg = mgr.get_annotated_frame(first_cam_id)
                if jpeg:
                    import numpy as np
                    frame = cv2.imdecode(
                        np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR
                    )
                    if frame is not None:
                        cv2.imshow("Face Detection – press q to quit", frame)

                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key == ord("s"):
                    print(mgr.summary())

                time.sleep(0.05)    # ~20 fps display refresh

        except KeyboardInterrupt:
            pass
        finally:
            cv2.destroyAllWindows()

    mgr.stop_all()
    print("\n" + mgr.summary())
    print("\nFinal brand statistics:")
    for bid, stats in mgr.brand_stats().items():
        print(
            f"  [{bid}] {stats['name']:20s}  "
            f"unique_visitors={stats['unique_count']}  "
            f"total_events={stats['detections']}"
        )


if __name__ == "__main__":
    main()
