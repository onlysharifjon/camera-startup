"""
Camera Attendance System — FastAPI Backend
==========================================
Barcha biznes logika shu yerda.
Frontend faqat API dan ma'lumot oladi.
"""
import asyncio, json, logging, base64
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import select

from backend.core.config          import settings
from backend.core.database        import init_db, AsyncSessionLocal
from backend.models.camera        import Camera
from backend.routers              import employees, attendance, cameras, users
from backend.services.camera_service     import camera_service
from backend.services.attendance_service import attendance_service
from backend.services.face_service       import face_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
)
log = logging.getLogger(__name__)

# ── WebSocket Connection Manager ───────────────────────────────────────────────
class WSManager:
    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket):
        self._connections.discard(ws) if hasattr(self._connections, 'discard') else None
        try: self._connections.remove(ws)
        except ValueError: pass

    async def broadcast(self, data: dict):
        dead = []
        for ws in self._connections:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

ws_manager = WSManager()

# ── Detection callback (kamera → davomat → WebSocket) ─────────────────────────
async def on_face_detected(camera_id: str, faces: list, snapshot: str, jpeg: bytes):
    """Har yuz aniqlanganda chaqiriladi."""
    # WebSocket orqali frontend ga frame yuborish
    b64 = base64.b64encode(jpeg).decode()
    await ws_manager.broadcast({
        "type":      "frame",
        "camera_id": camera_id,
        "faces":     faces,
        "image":     f"data:image/jpeg;base64,{b64}",
        "timestamp": datetime.now().isoformat(),
    })

    # Tanilgan yuzlar uchun davomat yozish
    async with AsyncSessionLocal() as db:
        for face in faces:
            emp_id = face.get("employee_id")
            if not emp_id:
                continue
            result = await attendance_service.process_recognition(
                db, emp_id, camera_id, snapshot
            )
            if result:
                await ws_manager.broadcast({
                    "type":   "attendance",
                    "data":   result,
                    "stats":  await attendance_service.today_stats(db),
                })

# ── App Lifecycle ──────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting Camera Attendance System...")

    # Ensure directories
    settings.CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    settings.FACES_DIR.mkdir(parents=True, exist_ok=True)

    # Init DB
    await init_db()

    # Set camera detection callback
    loop = asyncio.get_event_loop()
    camera_service.set_detection_callback(on_face_detected, loop)

    # Load cameras from DB and start streams
    async with AsyncSessionLocal() as db:
        rows = await db.execute(select(Camera).where(Camera.enabled == True))
        for cam in rows.scalars():
            if cam.type == "v380":
                camera_service.start_v380_watcher(cam.id)
            else:
                camera_service.start_camera(cam.id, cam.url)

    # Always start V380 watcher (screenshot folder)
    camera_service.start_v380_watcher("v380-darvoza")

    log.info(f"Server ready → http://{settings.HOST}:{settings.PORT}")
    yield

    camera_service.stop_all()
    log.info("Server stopped.")

# ── FastAPI App ────────────────────────────────────────────────────────────────
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(employees.router, prefix="/api")
app.include_router(attendance.router, prefix="/api")
app.include_router(cameras.router,    prefix="/api")
app.include_router(users.router,      prefix="/api")

# Captures static files
app.mount("/captures", StaticFiles(directory=str(settings.CAPTURES_DIR)), name="captures")

# ── WebSocket ──────────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    log.info(f"[WS] Client connected: {ws.client}")
    try:
        # Send initial stats on connect
        async with AsyncSessionLocal() as db:
            stats = await attendance_service.today_stats(db)
            today = await attendance_service.today_list(db)
        await ws.send_json({
            "type":  "init",
            "stats": stats,
            "today": today,
        })

        while True:
            await ws.receive_text()   # keep alive (ping)
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
        log.info(f"[WS] Client disconnected")

# ── Health ─────────────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"status": "ok", "version": settings.VERSION, "ts": datetime.now().isoformat()}

@app.get("/api/dashboard")
async def dashboard():
    """Frontend uchun barcha kerakli ma'lumot bir so'rovda."""
    async with AsyncSessionLocal() as db:
        stats = await attendance_service.today_stats(db)
        today = await attendance_service.today_list(db)
    return {
        "stats": stats,
        "today": today,
        "cameras": camera_service.status(),
    }

# ── Live Camera Viewer (standalone page) ───────────────────────────────────────
@app.get("/view/{cam_id}")
async def live_viewer(cam_id: str):
    """Kamera uchun to'liq ekran live viewer sahifasi."""
    from backend.models.camera import Camera
    from backend.core.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        cam = await db.get(Camera, cam_id)
    name     = cam.name     if cam else cam_id
    location = cam.location if cam else ""

    html = f"""<!DOCTYPE html>
<html lang="uz">
<head>
  <meta charset="UTF-8"/>
  <title>{name} — Live</title>
  <style>
    *{{margin:0;padding:0;box-sizing:border-box}}
    body{{background:#0d0d0d;color:#f0f0f0;font-family:system-ui,sans-serif;height:100vh;display:flex;flex-direction:column;overflow:hidden}}
    .topbar{{background:#141414;border-bottom:1px solid #2a2a2a;padding:0 16px;height:48px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0}}
    .tb-left{{display:flex;align-items:center;gap:12px;font-size:14px;font-weight:600}}
    .dot{{width:8px;height:8px;border-radius:50%;background:#44cc66;box-shadow:0 0 6px #44cc66;flex-shrink:0}}
    .dot.off{{background:#ff4455;box-shadow:none}}
    .loc{{font-size:12px;color:#555;font-weight:400}}
    .tb-right{{display:flex;align-items:center;gap:8px}}
    .people-badge{{background:#0a2a14;border:1px solid #1a5a2a;color:#44cc66;font-size:18px;font-weight:700;font-family:monospace;padding:2px 14px;border-radius:6px;min-width:60px;text-align:center}}
    .fps-lbl{{font-size:11px;color:#444;font-family:monospace}}
    button{{background:#1c1c1c;border:1px solid #2a2a2a;color:#888;padding:5px 10px;border-radius:5px;cursor:pointer;font-size:12px;transition:all .15s}}
    button:hover{{background:#252525;color:#eee;border-color:#444}}
    .btn-det{{color:#44cc66;border-color:#1a5a2a}}
    .btn-det.off{{color:#555;border-color:#2a2a2a}}
    .btn-close{{background:#c0392b;border-color:#c0392b;color:#fff;padding:4px 10px}}
    .body{{flex:1;display:flex;min-height:0}}
    .stream-wrap{{flex:1;background:#000;position:relative;overflow:hidden;cursor:crosshair}}
    #frame{{width:100%;height:100%;object-fit:contain;display:block}}
    .sidebar{{width:200px;flex-shrink:0;background:#111;border-left:1px solid #222;display:flex;flex-direction:column;overflow-y:auto}}
    .sb-section{{padding:12px;border-bottom:1px solid #1a1a1a}}
    .sb-title{{font-size:10px;font-weight:600;color:#444;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px}}
    .face-list{{display:flex;flex-direction:column;gap:4px}}
    .face-item{{background:#1a1a1a;border-radius:4px;padding:5px 8px;font-size:11px;color:#aaa;display:flex;align-items:center;gap:6px}}
    .face-item.known{{color:#44cc66;border-left:2px solid #44cc66}}
    .face-item.unknown{{color:#ff8844;border-left:2px solid #ff8844}}
    .ptz-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:3px}}
    .ptz-grid button{{padding:6px 0;font-size:14px}}
    .ir-btns{{display:flex;gap:4px}}
    .ir-btns button{{flex:1;padding:4px 0;font-size:11px}}
    .ir-auto{{color:#4499ff!important;border-color:#1a3a6a!important}}
    .ir-on {{color:#ffdd44!important;border-color:#4a3a00!important}}
    .bottom-bar{{background:#141414;border-top:1px solid #1a1a1a;padding:6px 12px;display:flex;align-items:center;gap:8px;flex-shrink:0;flex-wrap:wrap}}
    .bbar-label{{font-size:10px;color:#444;text-transform:uppercase;letter-spacing:.05em}}
    select{{background:#1c1c1c;border:1px solid #2a2a2a;color:#888;padding:4px 6px;border-radius:4px;font-size:11px}}
    .stat-pills{{display:flex;gap:6px;flex-wrap:wrap}}
    .pill{{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:4px;padding:2px 8px;font-size:11px;color:#666;font-family:monospace}}
    .pill.green{{border-color:#1a5a2a;color:#44cc66}}
  </style>
</head>
<body>
  <div class="topbar">
    <div class="tb-left">
      <div class="dot" id="dot"></div>
      <span>{name}</span>
      <span class="loc">{location}</span>
    </div>
    <div class="tb-right">
      <span style="font-size:11px;color:#555">Odamlar:</span>
      <span class="people-badge" id="peopleCount">0</span>
      <span class="fps-lbl" id="fpsLbl">– fps</span>
      <button class="btn-det" id="detBtn" onclick="toggleDetection()">🎯 Detection: ON</button>
      <button onclick="toggleFullscreen()">⛶</button>
      <button class="btn-close" onclick="window.close()">✕</button>
    </div>
  </div>

  <div class="body">
    <div class="stream-wrap" id="streamWrap">
      <img id="frame" src="" />
    </div>

    <div class="sidebar">
      <div class="sb-section">
        <div class="sb-title">Aniqlangan yuzlar</div>
        <div class="face-list" id="faceList">
          <div style="font-size:11px;color:#333">Hali yo'q</div>
        </div>
      </div>

      <div class="sb-section">
        <div class="sb-title">IR / Tungi</div>
        <div class="ir-btns">
          <button class="ir-auto" onclick="ctrl('ir_auto')">Auto</button>
          <button class="ir-on"   onclick="ctrl('ir_on')">Yoq</button>
          <button onclick="ctrl('ir_off')">O'ch</button>
        </div>
      </div>

      <div class="sb-section">
        <div class="sb-title">PTZ</div>
        <div class="ptz-grid">
          <span></span>
          <button onmousedown="ptz('up')"    onmouseup="ptz('stop')" ontouchstart="ptz('up')"    ontouchend="ptz('stop')">▲</button>
          <span></span>
          <button onmousedown="ptz('left')"  onmouseup="ptz('stop')" ontouchstart="ptz('left')"  ontouchend="ptz('stop')">◀</button>
          <button onclick="ptz('home')" style="font-size:10px;color:#4499ff">⌂</button>
          <button onmousedown="ptz('right')" onmouseup="ptz('stop')" ontouchstart="ptz('right')" ontouchend="ptz('stop')">▶</button>
          <span></span>
          <button onmousedown="ptz('down')"  onmouseup="ptz('stop')" ontouchstart="ptz('down')"  ontouchend="ptz('stop')">▼</button>
          <span></span>
        </div>
        <div style="display:flex;gap:4px;margin-top:4px">
          <button style="flex:1" onclick="ptz('zin')">🔍+</button>
          <button style="flex:1" onclick="ptz('zout')">🔍−</button>
        </div>
      </div>

      <div class="sb-section">
        <div class="sb-title">Tezkor</div>
        <div style="display:flex;flex-direction:column;gap:4px">
          <button onclick="takeSnap()">📷 Snapshot</button>
          <button onclick="ctrl('reboot')" style="color:#ff4455;border-color:#5a1a1a">↺ Reboot</button>
        </div>
      </div>

      <div class="sb-section">
        <div class="sb-title">Statistika</div>
        <div class="stat-pills" id="statPills">
          <span class="pill" id="sTotalToday">Bugun: 0</span>
          <span class="pill green" id="sOnline">Online</span>
        </div>
      </div>
    </div>
  </div>

  <div class="bottom-bar">
    <span class="bbar-label">Tezlik:</span>
    <select id="intervalSel">
      <option value="80">80ms (~12fps)</option>
      <option value="150" selected>150ms (~7fps)</option>
      <option value="300">300ms (~3fps)</option>
      <option value="500">500ms (2fps)</option>
    </select>
    <span class="bbar-label" style="margin-left:8px">Rejim:</span>
    <select id="modeSel">
      <option value="detect" selected>Detection (yuz boxlari)</option>
      <option value="raw">Raw (sof video)</option>
    </select>
    <span style="margin-left:auto;font-size:10px;color:#333">{cam_id[:8]}…</span>
  </div>

  <script>
    const CAM   = '{cam_id}';
    const img   = document.getElementById('frame');
    const dot   = document.getElementById('dot');
    const fpsEl = document.getElementById('fpsLbl');
    const cntEl = document.getElementById('peopleCount');

    let interval   = 150;
    let mode       = 'detect';   // 'detect' | 'raw'
    let detection  = true;
    let timer      = null;
    let facesTimer = null;
    let fCount     = 0;
    let lastFpsT   = Date.now();
    let isLoading  = false;

    // ── Stream ─────────────────────────────────────────────────────────────────
    function startStream() {{
      if (timer) clearInterval(timer);
      isLoading = false;
      timer = setInterval(() => {{
        if (isLoading) return;           // skip if prev not done
        isLoading = true;
        const url = mode === 'detect'
          ? `/api/cameras/${{CAM}}/detect?t=${{Date.now()}}`
          : `/api/cameras/${{CAM}}/snapshot?t=${{Date.now()}}`;
        const tmp = new Image();
        tmp.onload = () => {{
          img.src  = tmp.src;
          isLoading = false;
          fCount++;
          dot.className = 'dot';
        }};
        tmp.onerror = () => {{
          isLoading = false;
          dot.className = 'dot off';
        }};
        tmp.src = url;
      }}, interval);
    }}

    // FPS counter
    setInterval(() => {{
      const now = Date.now();
      const fps = (fCount / ((now - lastFpsT)/1000)).toFixed(1);
      fpsEl.textContent = fps + ' fps';
      fCount  = 0;
      lastFpsT = now;
    }}, 2000);

    // ── Face list (har 1 sek) ──────────────────────────────────────────────────
    async function fetchFaces() {{
      try {{
        const r = await fetch(`/api/cameras/${{CAM}}/faces`);
        if (!r.ok) return;
        const d = await r.json();
        cntEl.textContent = d.count;
        cntEl.style.color = d.count > 0 ? '#44cc66' : '#666';

        const list = document.getElementById('faceList');
        if (!d.faces.length) {{
          list.innerHTML = '<div style="font-size:11px;color:#333">Yuz aniqlanmadi</div>';
        }} else {{
          list.innerHTML = d.faces.map((f, i) => {{
            const known = !!f.employee_id;
            return `<div class="face-item ${{known ? 'known' : 'unknown'}}">
              <span>👤</span>
              <span>${{known ? f.employee_id.slice(0,8) : 'Noma\\'lum'}} (${{(f.confidence*100).toFixed(0)}}%)</span>
            </div>`;
          }}).join('');
        }}

        // Stats
        const statsR = await fetch(`/api/cameras/${{CAM}}/stats`);
        if (statsR.ok) {{
          const s = await statsR.json();
          document.getElementById('sTotalToday').textContent = `Bugun: ${{s.today.checkins}}`;
        }}
      }} catch(e) {{}}
    }}

    facesTimer = setInterval(fetchFaces, 1000);
    fetchFaces();

    // ── Controls ───────────────────────────────────────────────────────────────
    function toggleDetection() {{
      detection = !detection;
      mode      = detection ? 'detect' : 'raw';
      const btn = document.getElementById('detBtn');
      btn.className = detection ? 'btn-det' : 'btn-det off';
      btn.textContent = detection ? '🎯 Detection: ON' : '📷 Detection: OFF';
      startStream();
    }}

    async function ctrl(action) {{
      try {{
        await fetch(`/api/cameras/${{CAM}}/control?action=${{action}}`, {{method:'POST'}});
      }} catch(e) {{}}
    }}

    function ptz(dir) {{ ctrl('ptz_' + dir); }}

    function takeSnap() {{
      const a = document.createElement('a');
      a.href = `/api/cameras/${{CAM}}/snapshot?t=${{Date.now()}}`;
      a.download = `cam_${{Date.now()}}.jpg`;
      a.click();
    }}

    function toggleFullscreen() {{
      if (document.fullscreenElement) document.exitFullscreen();
      else document.getElementById('streamWrap').requestFullscreen();
    }}

    document.getElementById('intervalSel').onchange = e => {{
      interval = parseInt(e.target.value);
      startStream();
    }};

    document.getElementById('modeSel').onchange = e => {{
      mode = e.target.value;
      detection = mode === 'detect';
      startStream();
    }};

    startStream();
  </script>
</body>
</html>"""
    return HTMLResponse(html)


# ── Frontend SPA ───────────────────────────────────────────────────────────────
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

@app.get("/")
async def serve_frontend():
    return FileResponse(str(FRONTEND_DIR / "index.html"))
