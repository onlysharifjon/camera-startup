/* ─── UI Helpers ─────────────────────────────────────────────────────────────
   Render funksiyalari. Hech qanday biznes logika yo'q.
─────────────────────────────────────────────────────────────────────────── */

// ── Toast ─────────────────────────────────────────────────────────────────────
function toast(msg, type = 'info', ms = 3500) {
  const w  = document.getElementById('toastWrap');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  w.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, ms);
}

// ── Modal ─────────────────────────────────────────────────────────────────────
function openModal(id)  { document.getElementById(id)?.classList.add('open'); }
function closeModal(id) { document.getElementById(id)?.classList.remove('open'); }

document.addEventListener('click', e => {
  const btn = e.target.closest('.close-modal');
  if (btn) closeModal(btn.dataset.modal);
  if (e.target.classList.contains('overlay')) e.target.classList.remove('open');
});

// ── Status badge ──────────────────────────────────────────────────────────────
function statusBadge(status) {
  const map = {
    present:   ['badge-green',  'fa-circle-check',  'Keldi'],
    late:      ['badge-orange', 'fa-clock',         'Kech keldi'],
    early_out: ['badge-yellow', 'fa-right-from-bracket', 'Erta ketdi'],
    absent:    ['badge-red',    'fa-circle-xmark',  'Kelmadi'],
  };
  const [cls, icon, label] = map[status] || ['badge-gray', 'fa-circle', status];
  return `<span class="badge ${cls}"><i class="fa-solid ${icon}"></i> ${label}</span>`;
}

// ── Stats cards ───────────────────────────────────────────────────────────────
function renderStats(stats) {
  document.getElementById('s-total').textContent   = stats.total;
  document.getElementById('s-present').textContent = stats.present;
  document.getElementById('s-absent').textContent  = stats.absent;
  document.getElementById('s-late').textContent    = stats.late;
}

// ── Today table ───────────────────────────────────────────────────────────────
function renderToday(list) {
  document.getElementById('todayCount').textContent = `${list.length} ta`;
  const tbody = document.getElementById('todayBody');
  tbody.innerHTML = list.length === 0
    ? `<tr><td colspan="7" class="empty-cell">Bugun hali hech kim kelmadi</td></tr>`
    : list.map(r => `
      <tr>
        <td><strong>${esc(r.name)}</strong></td>
        <td>${esc(r.position)}</td>
        <td>${esc(r.department)}</td>
        <td class="mono">${r.checkin_time  || '–'}</td>
        <td class="mono">${r.checkout_time || '–'}</td>
        <td class="mono">${r.work_hours ? r.work_hours + 'h' : '–'}</td>
        <td>${statusBadge(r.status)}</td>
      </tr>`).join('');
}

// ── Employees table ───────────────────────────────────────────────────────────
function renderEmployees(list, filter = '') {
  const filtered = filter
    ? list.filter(e => e.full_name.toLowerCase().includes(filter.toLowerCase())
                    || e.department.toLowerCase().includes(filter.toLowerCase()))
    : list;

  document.getElementById('empBody').innerHTML = filtered.length === 0
    ? `<tr><td colspan="8" class="empty-cell">Xodimlar yo'q</td></tr>`
    : filtered.map(e => `
      <tr>
        <td><strong>${esc(e.full_name)}</strong></td>
        <td>${esc(e.position)}</td>
        <td>${esc(e.department)}</td>
        <td class="mono">${e.checkin_time}</td>
        <td class="mono">${e.checkout_time}</td>
        <td>${e.is_active
          ? '<span class="badge badge-green"><i class="fa-solid fa-circle-dot"></i> Faol</span>'
          : '<span class="badge badge-gray"><i class="fa-solid fa-circle"></i> Faol emas</span>'}</td>
        <td>${e.face_enrolled
          ? '<span class="badge badge-green"><i class="fa-solid fa-face-smile"></i> Kiritilgan</span>'
          : '<span class="badge badge-red"><i class="fa-solid fa-face-meh"></i> Kiritilmagan</span>'}</td>
        <td class="actions">
          <button class="btn btn-ghost btn-sm" onclick="editEmployee('${e.id}')">Tahrirlash</button>
          <button class="btn btn-ghost btn-sm" onclick="enrollFace('${e.id}')">Yuz</button>
          <button class="btn btn-danger btn-sm" onclick="deleteEmployee('${e.id}','${esc(e.full_name)}')">O'chir</button>
        </td>
      </tr>`).join('');
}

// ── Attendance history ────────────────────────────────────────────────────────
function renderHistory(list) {
  document.getElementById('histBody').innerHTML = list.length === 0
    ? `<tr><td colspan="9" class="empty-cell">Davomat yozuvi topilmadi</td></tr>`
    : list.map(r => `
      <tr>
        <td class="mono">${r.date}</td>
        <td>${esc(r.name)}</td>
        <td>${esc(r.department)}</td>
        <td class="mono">${r.checkin_time  || '–'}</td>
        <td class="mono">${r.checkout_time || '–'}</td>
        <td class="mono">${r.work_hours ? r.work_hours + 'h' : '–'}</td>
        <td class="mono ${r.late_minutes > 0 ? 'text-orange' : ''}">${r.late_minutes || 0} min</td>
        <td>${statusBadge(r.status)}</td>
        <td>
          <button class="btn btn-ghost btn-sm"
            onclick="openEditAtt('${r.id}','${r.checkin_time||''}','${r.checkout_time||''}')">
            Tahrirlash
          </button>
        </td>
      </tr>`).join('');
}

// ── Camera cards ──────────────────────────────────────────────────────────────
const CAP_DEFS = [
  { key: 'ptz',    icon: 'fa-arrows-up-down-left-right', label: 'PTZ'     },
  { key: 'ir',     icon: 'fa-lightbulb',                 label: 'IR'      },
  { key: 'audio',  icon: 'fa-microphone',                label: 'Audio'   },
  { key: 'motion', icon: 'fa-person-running',            label: 'Motion'  },
  { key: 'zoom',   icon: 'fa-magnifying-glass',          label: 'Zoom'    },
];

function capIcons(features = {}) {
  return CAP_DEFS.map(d => {
    const on = !!features[d.key];
    return `<span class="cv-cap ${on ? 'active' : ''}" title="${d.label}">
      <i class="fa-solid ${d.icon}"></i> ${d.label}
    </span>`;
  }).join('');
}

function renderCameras(list, statsMap = {}) {
  const container = document.getElementById('cameraCards');
  if (!list.length) {
    container.innerHTML = '<div class="empty">Kameralar qo\'shilmagan</div>';
    return;
  }
  container.innerHTML = list.map(c => {
    const s    = statsMap[c.id];
    const feat = c.features || {};

    const statsHtml = s ? `
      <div class="cam-stats">
        <div class="cam-stat-row">
          <div class="cam-stat">
            <div class="cam-stat-val">${s.today.checkins}</div>
            <div class="cam-stat-lbl"><i class="fa-solid fa-right-to-bracket"></i> Keldi</div>
          </div>
          <div class="cam-stat">
            <div class="cam-stat-val">${s.today.checkouts}</div>
            <div class="cam-stat-lbl"><i class="fa-solid fa-right-from-bracket"></i> Ketdi</div>
          </div>
          <div class="cam-stat">
            <div class="cam-stat-val">${s.today.unique_people}</div>
            <div class="cam-stat-lbl"><i class="fa-solid fa-users"></i> Noyob</div>
          </div>
          <div class="cam-stat">
            <div class="cam-stat-val">${s.all_time.total_captures}</div>
            <div class="cam-stat-lbl"><i class="fa-solid fa-images"></i> Surat</div>
          </div>
        </div>
        ${s.last_detection ? `<div class="cam-last-det"><i class="fa-solid fa-clock"></i> Oxirgi: ${new Date(s.last_detection).toLocaleString('uz')}</div>` : ''}
      </div>` : '';

    return `
    <div class="cam-card">
      <div class="cam-preview">
        <img src="/api/cameras/${c.id}/snapshot?t=${Date.now()}" alt="${esc(c.name)}"
             onerror="this.style.display='none'"
             style="width:100%;height:100%;object-fit:cover" />
        <span class="cam-status ${c.online ? 'online' : 'offline'}">
          <i class="fa-solid ${c.online ? 'fa-circle-play' : 'fa-circle-stop'}"></i>
          ${c.online ? 'Online' : 'Offline'}
        </span>
      </div>
      <div class="cam-info">
        <div class="cam-name">${esc(c.name)}</div>
        <div class="cam-meta">${esc(c.type.toUpperCase())} · ${esc(c.location || '–')}</div>
        <div class="cam-url mono">${esc(c.url)}</div>
        <div class="cv-caps" style="margin-top:8px">${capIcons(feat)}</div>
      </div>
      ${statsHtml}
      <div class="cam-actions">
        <button class="btn btn-primary btn-sm" style="flex:1" onclick="window.open('/view/${c.id}','_blank','width=1280,height=760')">
          <i class="fa-solid fa-circle-play"></i> Join
        </button>
        <button class="btn btn-ghost btn-sm" onclick="editCamera('${c.id}')">
          <i class="fa-solid fa-pen"></i>
        </button>
        <button class="btn btn-danger btn-sm" onclick="deleteCamera('${c.id}','${esc(c.name)}')">
          <i class="fa-solid fa-trash"></i>
        </button>
      </div>
    </div>`;
  }).join('');
}

// ── Live monitor ──────────────────────────────────────────────────────────────
function renderLiveFrame(payload) {
  const grid = document.getElementById('liveCameras');
  let cell = document.getElementById(`live-${payload.camera_id}`);

  if (!cell) {
    grid.innerHTML = '';
    cell = document.createElement('div');
    cell.className = 'live-cell';
    cell.id = `live-${payload.camera_id}`;
    cell.innerHTML = `
      <div class="live-cell-head">
        <span class="live-cam-name">${payload.camera_id}</span>
        <span class="live-face-count" id="fc-${payload.camera_id}">–</span>
      </div>
      <img class="live-img" id="img-${payload.camera_id}" src="" />
    `;
    grid.appendChild(cell);
  }

  document.getElementById(`img-${payload.camera_id}`).src = payload.image;
  const fc = payload.faces?.length || 0;
  const fcEl = document.getElementById(`fc-${payload.camera_id}`);
  if (fcEl) fcEl.textContent = fc > 0 ? `${fc} yuz` : 'Yuz yo\'q';
}

function appendLog(data) {
  const list = document.getElementById('logList');
  const li   = document.createElement('li');
  li.className = `log-item ${data.faces?.length ? 'has-face' : ''}`;
  const t = new Date(data.timestamp).toLocaleTimeString('uz');
  li.textContent = `[${t}]  ${data.camera_id}  →  ${data.faces?.length || 0} yuz`;
  list.prepend(li);
  while (list.children.length > 80) list.lastChild.remove();
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s||'')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
