/* ─── App ─────────────────────────────────────────────────────────────────────
   Faqat koordinatsiya: API chaqiruvlari + UI render.
   Hech qanday biznes logika yo'q.
─────────────────────────────────────────────────────────────────────────── */
'use strict';

// ── State ─────────────────────────────────────────────────────────────────────
let employees = [];
let currentPage = 'dashboard';

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('dateLabel').textContent =
    new Date().toLocaleDateString('uz-UZ', { weekday:'long', year:'numeric', month:'long', day:'numeric' });

  initNav();
  initWebSocket();
  loadDashboard();
  loadEmployees();
  bindModals();
  bindForms();
});

// ── Navigation ────────────────────────────────────────────────────────────────
function initNav() {
  document.querySelectorAll('.nav-item').forEach(a => {
    a.addEventListener('click', () => {
      const page = a.dataset.page;
      document.querySelectorAll('.nav-item').forEach(x => x.classList.remove('active'));
      document.querySelectorAll('.page').forEach(x => x.classList.remove('active'));
      a.classList.add('active');
      document.getElementById(`page-${page}`)?.classList.add('active');
      document.getElementById('pageTitle').textContent = a.textContent.trim();
      currentPage = page;

      if (page === 'dashboard') loadDashboard();
      if (page === 'employees') loadEmployees();
      if (page === 'attendance') loadHistory();
      if (page === 'cameras')   loadCameras();
    });
  });
}

// ── WebSocket ─────────────────────────────────────────────────────────────────
function initWebSocket() {
  const dot   = document.getElementById('wsDot');
  const label = document.getElementById('wsLabel');
  let ws, retryTimer;

  const connect = () => {
    ws = new WebSocket(`ws://${location.host}/ws`);

    ws.onopen = () => {
      dot.className = 'ws-dot online';
      label.textContent = 'Ulangan';
      clearTimeout(retryTimer);
    };

    ws.onmessage = ({ data }) => {
      const msg = JSON.parse(data);

      if (msg.type === 'init') {
        renderStats(msg.stats);
        renderToday(msg.today);
      }

      if (msg.type === 'frame') {
        if (currentPage === 'live') {
          renderLiveFrame(msg);
          appendLog(msg);
        }
      }

      if (msg.type === 'attendance') {
        renderStats(msg.stats);
        const icon = msg.data.action === 'checkin' ? 'fa-right-to-bracket' : 'fa-right-from-bracket';
        toast(`${msg.data.name} — ${msg.data.action === 'checkin' ? 'Keldi' : 'Ketdi'} (${new Date(msg.data.time).toLocaleTimeString('uz')})`, 'success', 5000);
        if (currentPage === 'dashboard') loadDashboard();
      }
    };

    ws.onerror  = () => {};
    ws.onclose  = () => {
      dot.className = 'ws-dot offline';
      label.textContent = 'Ulanmadi';
      retryTimer = setTimeout(connect, 4000);
    };
  };

  connect();
  setInterval(() => { if (ws.readyState === WebSocket.OPEN) ws.send('ping'); }, 25000);
}

// ── Dashboard ─────────────────────────────────────────────────────────────────
async function loadDashboard() {
  try {
    const data = await api.dashboard();
    renderStats(data.stats);
    renderToday(data.today);
  } catch (e) { toast(e.message, 'error'); }
}

// ── Employees ─────────────────────────────────────────────────────────────────
async function loadEmployees() {
  try {
    const data = await api.listEmployees();
    employees = data.employees;
    renderEmployees(employees);
    populateEmpFilter(employees);
  } catch (e) { toast(e.message, 'error'); }
}

function populateEmpFilter(list) {
  const sel = document.getElementById('histEmp');
  const cur = sel.value;
  sel.innerHTML = '<option value="">Barcha xodimlar</option>' +
    list.map(e => `<option value="${e.id}" ${e.id===cur?'selected':''}>${esc(e.full_name)}</option>`).join('');
}

document.getElementById('empSearch').addEventListener('input', e => {
  renderEmployees(employees, e.target.value);
});

window.editEmployee = (id) => {
  const e = employees.find(x => x.id === id);
  if (!e) return;
  document.getElementById('empId').value       = e.id;
  document.getElementById('empName').value     = e.full_name;
  document.getElementById('empPosition').value = e.position;
  document.getElementById('empDept').value     = e.department;
  document.getElementById('empPhone').value    = e.phone;
  document.getElementById('empCheckin').value  = e.checkin_time;
  document.getElementById('empCheckout').value = e.checkout_time;
  document.getElementById('enrollSection').style.display = 'none';
  document.getElementById('empModalTitle').textContent = 'Xodimni tahrirlash';
  openModal('empModal');
};

window.enrollFace = (id) => {
  const e = employees.find(x => x.id === id);
  if (!e) return;
  document.getElementById('empId').value       = e.id;
  document.getElementById('empName').value     = e.full_name;
  document.getElementById('empPosition').value = e.position;
  document.getElementById('empDept').value     = e.department;
  document.getElementById('empPhone').value    = e.phone;
  document.getElementById('empCheckin').value  = e.checkin_time;
  document.getElementById('empCheckout').value = e.checkout_time;
  document.getElementById('enrollSection').style.display = 'block';
  document.getElementById('empModalTitle').textContent = 'Yuz kiritish';
  openModal('empModal');
};

window.deleteEmployee = async (id, name) => {
  if (!confirm(`"${name}" xodimini o'chirasizmi?`)) return;
  try {
    await api.deleteEmployee(id);
    toast('Xodim o\'chirildi', 'success');
    loadEmployees();
  } catch(e) { toast(e.message, 'error'); }
};

// ── Attendance ────────────────────────────────────────────────────────────────
async function loadHistory() {
  const empId = document.getElementById('histEmp').value;
  const from  = document.getElementById('histFrom').value;
  const to    = document.getElementById('histTo').value;
  try {
    const data = await api.historyList(empId, from, to);
    renderHistory(data.history);
  } catch(e) { toast(e.message, 'error'); }
}

document.getElementById('histFilter').addEventListener('click', loadHistory);

window.openEditAtt = (id, checkin, checkout) => {
  document.getElementById('attId').value       = id;
  document.getElementById('attCheckin').value  = checkin;
  document.getElementById('attCheckout').value = checkout;
  document.getElementById('attNote').value     = '';
  openModal('attModal');
};

// ── Camera Viewer ─────────────────────────────────────────────────────────────
let _cvCamId     = null;
let _cvSnapTimer = null;

function _cvStopStream() {
  if (_cvSnapTimer) { clearInterval(_cvSnapTimer); _cvSnapTimer = null; }
  const img = document.getElementById('cvStream');
  if (img) img.src = '';
}

function _cvStartStream(id) {
  const img = document.getElementById('cvStream');
  if (!img) return;

  // Snapshot polling — universally works in all browsers
  const refresh = () => {
    img.src = `/api/cameras/${id}/snapshot?t=${Date.now()}`;
  };
  refresh();
  _cvSnapTimer = setInterval(refresh, 150);   // ~7 fps
}

window.openCameraViewer = async (id) => {
  _cvCamId = id;
  _cvStopStream();

  const data = await api.listCameras();
  const cam  = data.cameras.find(c => c.id === id);
  if (!cam) return;

  const feat = cam.features || {};

  // Header
  document.getElementById('cvName').textContent     = cam.name;
  document.getElementById('cvLocation').textContent = cam.location || '';
  const onlineBadge = document.getElementById('cvOnline');
  onlineBadge.className = `badge ${cam.online ? 'badge-green' : 'badge-red'}`;
  onlineBadge.innerHTML = `<i class="fa-solid fa-circle"></i> ${cam.online ? 'Online' : 'Offline'}`;

  // Capabilities
  document.getElementById('cvCaps').innerHTML = capIcons(feat);

  // Sections visibility
  document.getElementById('cvPtzSection').style.display   = feat.ptz   ? '' : 'none';
  document.getElementById('cvZoomSection').style.display  = feat.zoom  ? '' : 'none';
  document.getElementById('cvIrSection').style.display    = feat.ir    ? '' : 'none';
  document.getElementById('cvAudioSection').style.display = feat.audio ? '' : 'none';

  openModal('camViewModal');
  _cvStartStream(id);
};

// Modal yopilganda stream to'xtatish
document.addEventListener('click', e => {
  const closer = e.target.closest('.close-modal');
  if (closer?.dataset?.modal === 'camViewModal') _cvStopStream();
  if (e.target.id === 'camViewModal') _cvStopStream();
});

// PTZ tugmalar
['up','down','left','right','home','zin','zout'].forEach(dir => {
  const btn = document.getElementById(`ptz-${dir}`);
  if (!btn) return;

  const sendPtz = async (action) => {
    if (!_cvCamId) return;
    try { await api.cameraControl(_cvCamId, `ptz_${action}`); }
    catch(e) { toast(e.message, 'error'); }
  };

  // Bosib turish → start, qo'yib yuborish → stop
  btn.addEventListener('mousedown',  () => sendPtz(dir));
  btn.addEventListener('touchstart', () => sendPtz(dir), { passive: true });
  btn.addEventListener('mouseup',    () => sendPtz('stop'));
  btn.addEventListener('mouseleave', () => sendPtz('stop'));
  btn.addEventListener('touchend',   () => sendPtz('stop'));
});

// IR tugmalar
document.querySelectorAll('.cv-ir-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    if (!_cvCamId) return;
    document.querySelectorAll('.cv-ir-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    try {
      await api.cameraControl(_cvCamId, btn.dataset.action);
      toast('IR rejim o\'zgartirildi', 'success');
    } catch(e) { toast(e.message, 'error'); }
  });
});

// Snapshot
document.getElementById('cvSnapshot')?.addEventListener('click', async () => {
  if (!_cvCamId) return;
  const url = api.snapshotUrl(_cvCamId);
  const a   = document.createElement('a');
  a.href     = url;
  a.download = `snapshot_${_cvCamId}_${Date.now()}.jpg`;
  a.click();
  toast('Snapshot saqlandi', 'success');
});

// Reboot
document.getElementById('cvReboot')?.addEventListener('click', async () => {
  if (!_cvCamId) return;
  if (!confirm('Kamerani qayta yoqishni tasdiqlaysizmi?')) return;
  try {
    await api.cameraControl(_cvCamId, 'reboot');
    toast('Kamera qayta yoqilmoqda…', 'info');
  } catch(e) { toast(e.message, 'error'); }
});

// Fullscreen
document.getElementById('cvFullscreen')?.addEventListener('click', () => {
  const wrap = document.querySelector('.camview-stream');
  if (!wrap) return;
  if (document.fullscreenElement) {
    document.exitFullscreen();
  } else {
    wrap.requestFullscreen?.();
  }
});

// ── Cameras ───────────────────────────────────────────────────────────────────
async function loadCameras() {
  try {
    const data = await api.listCameras();
    // Har bir kamera uchun statistikani parallel yuklash
    const statsResults = await Promise.allSettled(
      data.cameras.map(c => api.cameraStats(c.id))
    );
    const statsMap = {};
    data.cameras.forEach((c, i) => {
      if (statsResults[i].status === 'fulfilled') {
        statsMap[c.id] = statsResults[i].value;
      }
    });
    renderCameras(data.cameras, statsMap);
  } catch(e) { toast(e.message, 'error'); }
}

window.editCamera = (id) => {
  // load and open
  api.listCameras().then(d => {
    const c = d.cameras.find(x => x.id === id);
    if (!c) return;
    document.getElementById('camId').value       = c.id;
    document.getElementById('camName').value     = c.name;
    document.getElementById('camUrl').value      = c.url;
    document.getElementById('camType').value     = c.type;
    document.getElementById('camLocation').value = c.location;
    document.getElementById('camModalTitle').textContent = 'Kamerani tahrirlash';
    openModal('camModal');
  });
};

window.deleteCamera = async (id, name) => {
  if (!confirm(`"${name}" kamerasini o'chirasizmi?`)) return;
  try {
    await api.deleteCamera(id);
    toast('Kamera o\'chirildi', 'success');
    loadCameras();
  } catch(e) { toast(e.message, 'error'); }
};

document.getElementById('clearLog').addEventListener('click', () => {
  document.getElementById('logList').innerHTML = '';
});

// ── Forms ─────────────────────────────────────────────────────────────────────
function bindForms() {
  // Add camera button
  document.getElementById('addCamBtn').addEventListener('click', () => {
    document.getElementById('camId').value = '';
    document.getElementById('camName').value = '';
    document.getElementById('camUrl').value = '';
    document.getElementById('camType').value = 'rtsp';
    document.getElementById('camLocation').value = '';
    document.getElementById('camModalTitle').textContent = 'Kamera qo\'shish';
    openModal('camModal');
  });

  // Add employee button
  document.getElementById('addEmpBtn').addEventListener('click', () => {
    document.getElementById('empId').value = '';
    document.getElementById('empName').value = '';
    document.getElementById('empPosition').value = '';
    document.getElementById('empDept').value = '';
    document.getElementById('empPhone').value = '';
    document.getElementById('empCheckin').value = '09:00';
    document.getElementById('empCheckout').value = '18:00';
    document.getElementById('enrollSection').style.display = 'none';
    document.getElementById('empModalTitle').textContent = 'Xodim qo\'shish';
    openModal('empModal');
  });

  // Save employee
  document.getElementById('saveEmpBtn').addEventListener('click', async () => {
    const id   = document.getElementById('empId').value;
    const body = {
      full_name:     document.getElementById('empName').value.trim(),
      position:      document.getElementById('empPosition').value.trim(),
      department:    document.getElementById('empDept').value.trim(),
      phone:         document.getElementById('empPhone').value.trim(),
      checkin_time:  document.getElementById('empCheckin').value,
      checkout_time: document.getElementById('empCheckout').value,
    };
    if (!body.full_name) { toast('F.I.O kiritilmagan', 'error'); return; }
    try {
      const emp = id ? await api.updateEmployee(id, body) : await api.createEmployee(body);

      // Enroll face if file selected
      const file = document.getElementById('empFaceFile').files[0];
      if (file) {
        await api.enrollFace(emp.id || id, file);
        toast('Yuz muvaffaqiyatli kiritildi', 'success');
      }

      closeModal('empModal');
      toast(id ? 'Xodim yangilandi' : 'Xodim qo\'shildi', 'success');
      loadEmployees();
    } catch(e) { toast(e.message, 'error'); }
  });

  // Save camera
  document.getElementById('saveCamBtn').addEventListener('click', async () => {
    const id   = document.getElementById('camId').value;
    const body = {
      name:     document.getElementById('camName').value.trim(),
      url:      document.getElementById('camUrl').value.trim(),
      type:     document.getElementById('camType').value,
      location: document.getElementById('camLocation').value.trim(),
    };
    if (!body.name || !body.url) { toast('Nomi va URL kiritilmagan', 'error'); return; }
    try {
      id ? await api.updateCamera(id, body) : await api.createCamera(body);
      closeModal('camModal');
      toast(id ? 'Kamera yangilandi' : 'Kamera qo\'shildi', 'success');
      loadCameras();
    } catch(e) { toast(e.message, 'error'); }
  });

  // Save attendance edit
  document.getElementById('saveAttBtn').addEventListener('click', async () => {
    const id   = document.getElementById('attId').value;
    const body = {};
    const ci   = document.getElementById('attCheckin').value;
    const co   = document.getElementById('attCheckout').value;
    const note = document.getElementById('attNote').value;
    if (ci)   body.checkin_time  = ci;
    if (co)   body.checkout_time = co;
    if (note) body.note = note;
    try {
      await api.updateAttendance(id, body);
      closeModal('attModal');
      toast('Davomat yangilandi', 'success');
      loadHistory();
    } catch(e) { toast(e.message, 'error'); }
  });
}

function bindModals() {}   // handled by ui.js document click
