import asyncio, json, logging, os
from datetime import date
from urllib.parse import urlparse
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database       import get_db
from backend.core.config         import settings
from backend.core.auth           import get_current_brand, optional_auth
from backend.models.camera       import Camera, DEFAULT_FEATURES
from backend.models.brand        import Brand
from backend.models.attendance   import Attendance
from backend.services.camera_service import camera_service

router = APIRouter(prefix="/cameras", tags=["cameras"])
log    = logging.getLogger(__name__)


# ── Pydantic models ────────────────────────────────────────────────────────────

class CameraCreate(BaseModel):
    name:     str
    url:      str
    type:     str  = "rtsp"
    location: str  = ""
    enabled:  bool = True
    features: Optional[dict] = None
    brand_id: Optional[str]  = None

class CameraUpdate(BaseModel):
    name:     Optional[str]  = None
    url:      Optional[str]  = None
    location: Optional[str]  = None
    enabled:  Optional[bool] = None
    features: Optional[dict] = None
    brand_id: Optional[str]  = None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_features(c: Camera) -> dict:
    try:
        return json.loads(c.features or DEFAULT_FEATURES)
    except Exception:
        return json.loads(DEFAULT_FEATURES)

def _cam_dict(c: Camera) -> dict:
    return {
        "id":        c.id,
        "name":      c.name,
        "url":       c.url,
        "type":      c.type,
        "location":  c.location,
        "enabled":   c.enabled,
        "online":    camera_service.status().get(c.id, False),
        "features":  _parse_features(c),
        "brand_id":  c.brand_id,
        "brand_name": c.brand.name if c.brand else None,
    }

def _camera_base_url(url: str) -> tuple[str, tuple[str, str]]:
    """RTSP URL dan IP va auth ajratib olish."""
    p = urlparse(url)
    base = f"http://{p.hostname}"
    auth = (p.username or "admin", p.password or "")
    return base, auth


# ── CRUD endpoints ─────────────────────────────────────────────────────────────

@router.get("")
async def list_cameras(
    db:    AsyncSession = Depends(get_db),
    token: Optional[dict] = Depends(optional_auth),
):
    """Kameralar ro'yxati. Brand login qilgan bo'lsa — faqat o'zining kameralari."""
    stmt = select(Camera)
    if token and not token.get("su"):          # brand user, not superuser
        stmt = stmt.where(Camera.brand_id == token["sub"])
    rows = await db.execute(stmt)
    return {"cameras": [_cam_dict(c) for c in rows.scalars()]}


@router.post("", status_code=201)
async def add_camera(body: CameraCreate, db: AsyncSession = Depends(get_db)):
    data = body.model_dump()
    feat = data.pop("features", None)
    cam  = Camera(**data)
    if feat is not None:
        cam.features = json.dumps(feat)
    db.add(cam)
    await db.commit()
    await db.refresh(cam)
    if cam.enabled:
        camera_service.start_camera(cam.id, cam.url)
    return _cam_dict(cam)


@router.patch("/{cam_id}")
async def update_camera(cam_id: str, body: CameraUpdate, db: AsyncSession = Depends(get_db)):
    cam = await db.get(Camera, cam_id)
    if not cam:
        raise HTTPException(404, "Camera not found")
    data = body.model_dump(exclude_none=True)
    feat = data.pop("features", None)
    for field, val in data.items():
        setattr(cam, field, val)
    if feat is not None:
        cam.features = json.dumps(feat)
    await db.commit()
    if cam.enabled:
        camera_service.start_camera(cam.id, cam.url)
    else:
        camera_service.stop_camera(cam.id)
    return _cam_dict(cam)


@router.delete("/{cam_id}")
async def delete_camera(cam_id: str, db: AsyncSession = Depends(get_db)):
    cam = await db.get(Camera, cam_id)
    if not cam:
        raise HTTPException(404, "Camera not found")
    camera_service.stop_camera(cam_id)
    await db.delete(cam)
    await db.commit()
    return {"deleted": True}


# ── Stream endpoints ───────────────────────────────────────────────────────────

@router.get("/{cam_id}/snapshot")
async def get_snapshot(cam_id: str):
    frame = camera_service.get_latest_frame(cam_id)
    if not frame:
        raise HTTPException(503, "No frame available")
    return Response(content=frame, media_type="image/jpeg",
                    headers={"Cache-Control": "no-cache, no-store"})


@router.get("/{cam_id}/detect")
async def get_annotated(cam_id: str):
    """Yuz detection box'lari va odamlar soni bilan frame."""
    frame = camera_service.get_annotated_frame(cam_id)
    if not frame:
        raise HTTPException(503, "No frame available")
    return Response(content=frame, media_type="image/jpeg",
                    headers={"Cache-Control": "no-cache, no-store"})


@router.get("/{cam_id}/faces")
async def get_faces(cam_id: str):
    """Joriy aniqlangan yuzlar ro'yxati va soni."""
    faces = camera_service.get_latest_faces(cam_id)
    return {
        "camera_id":   cam_id,
        "count":       len(faces),
        "faces":       faces,
        "online":      camera_service.status().get(cam_id, False),
    }


@router.get("/{cam_id}/mjpeg")
async def mjpeg_stream(cam_id: str):
    """MJPEG stream — brauzerda <img src="..."> bilan ko'rsatiladi."""
    async def generate():
        try:
            while True:
                frame = camera_service.get_latest_frame(cam_id)
                if frame:
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n" +
                        frame +
                        b"\r\n"
                    )
                await asyncio.sleep(0.04)   # ~25 fps max
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store", "Pragma": "no-cache"},
    )


# ── Camera stats ───────────────────────────────────────────────────────────────

@router.get("/{cam_id}/stats")
async def camera_stats(cam_id: str, db: AsyncSession = Depends(get_db)):
    cam = await db.get(Camera, cam_id)
    if not cam:
        raise HTTPException(404, "Camera not found")
    today = date.today()

    checkins_today = await db.scalar(
        select(func.count()).where(
            Attendance.checkin_camera_id == cam_id,
            Attendance.date == today,
        )
    ) or 0
    unique_today = await db.scalar(
        select(func.count(func.distinct(Attendance.employee_id))).where(
            Attendance.checkin_camera_id == cam_id,
            Attendance.date == today,
        )
    ) or 0
    last_row = await db.execute(
        select(Attendance.checkin_time).where(
            Attendance.checkin_camera_id == cam_id,
        ).order_by(Attendance.checkin_time.desc()).limit(1)
    )
    last_time = last_row.scalar_one_or_none()
    total_checkins = await db.scalar(
        select(func.count()).where(Attendance.checkin_camera_id == cam_id)
    ) or 0
    checkouts_today = await db.scalar(
        select(func.count()).where(
            Attendance.checkin_camera_id == cam_id,
            Attendance.date == today,
            Attendance.checkout_time.isnot(None),
        )
    ) or 0

    capture_dir    = settings.CAPTURES_DIR / cam_id
    total_captures = 0
    if capture_dir.exists():
        for root, dirs, files in os.walk(capture_dir):
            total_captures += sum(1 for f in files if f.endswith(".jpg"))

    return {
        "camera_id":   cam_id,
        "camera_name": cam.name,
        "online":      camera_service.status().get(cam_id, False),
        "today": {
            "checkins":      checkins_today,
            "checkouts":     checkouts_today,
            "unique_people": unique_today,
        },
        "all_time": {
            "total_checkins": total_checkins,
            "total_captures": total_captures,
        },
        "last_detection": last_time.isoformat() if last_time else None,
    }


# ── Camera control (Dahua CGI) ─────────────────────────────────────────────────

PTZ_CODES = {
    "up":    "Up",    "down":  "Down",
    "left":  "Left",  "right": "Right",
    "home":  "GotoPreset",
    "zin":   "ZoomTele", "zout": "ZoomWide",
}

@router.post("/{cam_id}/control")
async def camera_control(
    cam_id: str,
    action: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Kamera boshqaruvi (Dahua HTTP CGI).
    action: ptz_up | ptz_down | ptz_left | ptz_right | ptz_home |
            ptz_zin | ptz_zout | ptz_stop |
            ir_auto | ir_on | ir_off |
            reboot
    """
    cam = await db.get(Camera, cam_id)
    if not cam:
        raise HTTPException(404, "Camera not found")

    base, auth = _camera_base_url(cam.url)

    try:
        async with httpx.AsyncClient(timeout=4) as client:

            # ── PTZ ─────────────────────────────────────────────────────────
            if action.startswith("ptz_"):
                key  = action[4:]          # up / down / stop / etc.
                code = PTZ_CODES.get(key)
                if not code:
                    raise HTTPException(400, f"Unknown PTZ action: {key}")

                if key == "stop":
                    # stop all directions
                    for c in ["Up","Down","Left","Right"]:
                        await client.get(
                            f"{base}/cgi-bin/ptz.cgi",
                            params={"action":"stop","channel":0,"code":c,"arg1":0,"arg2":4,"arg3":0},
                            auth=auth,
                        )
                elif key == "home":
                    await client.get(
                        f"{base}/cgi-bin/ptz.cgi",
                        params={"action":"start","channel":0,"code":"GotoPreset","arg1":0,"arg2":1,"arg3":0},
                        auth=auth,
                    )
                else:
                    await client.get(
                        f"{base}/cgi-bin/ptz.cgi",
                        params={"action":"start","channel":0,"code":code,"arg1":0,"arg2":4,"arg3":0},
                        auth=auth,
                    )

            # ── IR / Night light ────────────────────────────────────────────
            elif action.startswith("ir_"):
                mode_map = {"ir_auto": "Auto", "ir_on": "Manual", "ir_off": "Close"}
                mode = mode_map.get(action)
                if not mode:
                    raise HTTPException(400, f"Unknown IR action: {action}")
                await client.get(
                    f"{base}/cgi-bin/configManager.cgi",
                    params={"action": "setConfig", "Infrared[0].Mode": mode},
                    auth=auth,
                )

            # ── Reboot ──────────────────────────────────────────────────────
            elif action == "reboot":
                await client.get(
                    f"{base}/cgi-bin/magicBox.cgi",
                    params={"action": "reboot"},
                    auth=auth,
                )

            else:
                raise HTTPException(400, f"Unknown action: {action}")

    except httpx.ConnectError:
        raise HTTPException(503, "Camera not reachable")
    except httpx.TimeoutException:
        raise HTTPException(504, "Camera timeout")

    return {"ok": True, "action": action}
