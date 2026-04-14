/* ─── API Client ─────────────────────────────────────────────────────────────
   Barcha backend so'rovlari shu faylda.
   Frontend bu fayldan tashqarida hech qanday fetch qilmaydi.
─────────────────────────────────────────────────────────────────────────── */
const API_BASE = '';   // same origin

const api = {
  // ── Generic ──────────────────────────────────────────────────────────────
  async _req(method, path, body, isFormData = false) {
    const opts = { method, headers: {} };
    if (body && !isFormData) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    } else if (isFormData) {
      opts.body = body;   // FormData – no Content-Type header
    }
    const res = await fetch(API_BASE + path, opts);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || err.error || res.statusText);
    }
    return res.json();
  },

  // ── Dashboard ────────────────────────────────────────────────────────────
  dashboard:   ()          => api._req('GET',  '/api/dashboard'),

  // ── Employees ────────────────────────────────────────────────────────────
  listEmployees:   ()      => api._req('GET',  '/api/employees'),
  createEmployee:  (data)  => api._req('POST', '/api/employees', data),
  updateEmployee:  (id, d) => api._req('PATCH',`/api/employees/${id}`, d),
  deleteEmployee:  (id)    => api._req('DELETE',`/api/employees/${id}`),
  enrollFace: (id, file) => {
    const fd = new FormData();
    fd.append('file', file);
    return api._req('POST', `/api/employees/${id}/enroll`, fd, true);
  },

  // ── Attendance ───────────────────────────────────────────────────────────
  todayStats:      ()           => api._req('GET', '/api/attendance/today/stats'),
  todayList:       ()           => api._req('GET', '/api/attendance/today'),
  historyList: (empId, from, to) => {
    const p = new URLSearchParams();
    if (empId) p.set('employee_id', empId);
    if (from)  p.set('date_from', from);
    if (to)    p.set('date_to', to);
    return api._req('GET', `/api/attendance/history?${p}`);
  },
  updateAttendance: (id, data) => {
    const p = new URLSearchParams(data);
    return api._req('PATCH', `/api/attendance/${id}?${p}`);
  },

  // ── Cameras ──────────────────────────────────────────────────────────────
  listCameras:   ()          => api._req('GET',   '/api/cameras'),
  createCamera:  (data)      => api._req('POST',  '/api/cameras', data),
  updateCamera:  (id, d)     => api._req('PATCH', `/api/cameras/${id}`, d),
  deleteCamera:  (id)        => api._req('DELETE',`/api/cameras/${id}`),
  cameraStats:   (id)        => api._req('GET',   `/api/cameras/${id}/stats`),
  cameraControl: (id, action)=> api._req('POST',  `/api/cameras/${id}/control?action=${action}`),
  snapshotUrl:   (id)        => `${API_BASE}/api/cameras/${id}/snapshot?t=${Date.now()}`,
  mjpegUrl:      (id)        => `${API_BASE}/api/cameras/${id}/mjpeg`,
};
