// 達揚藥局個人班表 — 員工端 vanilla JS
// 流程：載 current.json → 沒名字就選名字 → 月曆檢視 → 共班開關 / 產圖 / CSV

'use strict';

// ── 設定 ───────────────────────────────────────────────
const INDEX_URL = 'data/index.json';
const FALLBACK_DATA_URL = 'data/current.json';
const STORAGE_KEY = 'dayang_user_name';
const COWORKER_THRESHOLD_HOURS = 5;

const LOC_FULL = { '達': '達揚', '日': '日揚', '健': '健揚' };
const LOC_SHORT = { '達揚': '達', '日揚': '日', '健揚': '健' };
const LOC_NAME_MAP = { '達揚': '達揚藥局', '日揚': '日揚藥局', '健揚': '健揚藥局' };
const LOC_COLOR = { '達揚': '#1E90FF', '日揚': '#FF8C00', '健揚': '#2E8B57' };

const SHIFT_TIMES = {
  '達早': [9 * 60, 17 * 60], '達晚': [14 * 60, 22 * 60], '達全': [9 * 60, 22 * 60],
  '健早': [9 * 60, 17 * 60], '健晚': [14 * 60, 22 * 60], '健全': [9 * 60, 22 * 60],
  '日早': [8 * 60, 17 * 60], '日晚': [14 * 60, 22 * 60], '日全': [8 * 60, 22 * 60],
};
const OFF_TYPES = new Set(['休', '必休', '年假', '補休']);

// ── 狀態 ───────────────────────────────────────────────
const state = {
  index: null,
  data: null,
  myName: null,
  showCoworkers: false,
};

// ── 工具：時段 ─────────────────────────────────────────
function isOffType(t) { return OFF_TYPES.has(t); }

function parseHours(s) {
  if (!s || !s.includes('-')) return null;
  const [a, b] = s.split('-').map(x => x.trim());
  const [sh, sm] = splitHM(a);
  let [eh, em] = splitHM(b);
  if (sh === null || eh === null) return null;
  let startH = sh, endH = eh;
  // 24h 字面：起首 0，或無冒號且 ≥ 4 位（3 位如「400」仍視為下午簡寫）
  const is24hLiteral = a.startsWith('0') || (!a.includes(':') && a.length >= 4);
  if (!is24hLiteral && startH < 8) { startH += 12; endH += 12; }
  if (startH < 0 || startH > 23 || endH < 0 || endH > 23) return null;
  if (sm < 0 || sm > 59 || em < 0 || em > 59) return null;
  const start = startH * 60 + sm;
  const end = endH * 60 + em;
  if (end <= start) return null;
  return [start, end];
}

function splitHM(t) {
  if (t.includes(':')) {
    const [h, m] = t.split(':');
    const hi = parseInt(h, 10), mi = parseInt(m, 10);
    if (isNaN(hi) || isNaN(mi)) return [null, null];
    return [hi, mi];
  }
  const n = parseInt(t, 10);
  if (isNaN(n) || n < 0) return [null, null];
  if (n <= 23) return [n, 0];
  if (n >= 100 && n <= 2359) return [Math.floor(n / 100), n % 100];
  return [null, null];
}

function getShiftWindow(name, loc, type, hours, weekday) {
  if (isOffType(type) || !type) return null;
  if (hours) {
    const p = parseHours(hours);
    if (p) return p;
  }
  if (!loc) return null;
  const short = LOC_SHORT[loc] || loc;
  const key = short + type;
  // 志銓平日日晚特例
  if (name === '志銓' && key === '日晚' && weekday !== null && weekday >= 1 && weekday <= 5) {
    return [17 * 60, 22 * 60];
  }
  return SHIFT_TIMES[key] || null;
}

function minToHHMM(min) {
  const h = Math.floor(min / 60), m = min % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

function minTo12h(min) {
  const h24 = Math.floor(min / 60), m = min % 60;
  const ampm = h24 < 12 ? 'AM' : 'PM';
  let h = h24 % 12; if (h === 0) h = 12;
  return `${h}:${String(m).padStart(2, '0')} ${ampm}`;
}

// ── 工具：日期 ─────────────────────────────────────────
function weekdayOf(year, month, day) {
  // 0=Sun..6=Sat（一週從週日起）
  return new Date(year, month - 1, day).getDay();
}

function lastDay(year, month) {
  return new Date(year, month, 0).getDate();
}

// ── 共班計算 ───────────────────────────────────────────
function computeCoworkers(shifts, year, month) {
  const thresholdMin = COWORKER_THRESHOLD_HOURS * 60;
  const byDay = {};

  for (const s of shifts) {
    if (s.no_coworker_calc || s.type === '雙') continue;
    if (isOffType(s.type) || !s.type) continue;
    if (!s.loc) continue;
    const wd = weekdayOf(year, month, s.day);
    const win = getShiftWindow(s.name, s.loc, s.type, s.hours, wd);
    if (!win) continue;
    if (!byDay[s.day]) byDay[s.day] = [];
    byDay[s.day].push({ name: s.name, loc: s.loc, start: win[0], end: win[1] });
  }

  const result = {};
  for (const day of Object.keys(byDay)) {
    const entries = byDay[day];
    for (let i = 0; i < entries.length; i++) {
      const a = entries[i];
      const mates = [];
      for (let j = 0; j < entries.length; j++) {
        if (i === j) continue;
        const b = entries[j];
        if (a.loc !== b.loc) continue;
        if (a.name === b.name) continue;
        const overlap = Math.min(a.end, b.end) - Math.max(a.start, b.start);
        if (overlap >= thresholdMin) mates.push(b.name);
      }
      if (mates.length) {
        result[`${a.name}|${day}`] = [...new Set(mates)].sort();
      }
    }
  }
  return result;
}

// ── 載入 ───────────────────────────────────────────────
async function loadIndex() {
  try {
    const resp = await fetch(INDEX_URL, { cache: 'no-cache' });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } catch (e) {
    return null;
  }
}

async function loadMonthFile(file) {
  const resp = await fetch(`data/${file}`, { cache: 'no-cache' });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return await resp.json();
}

function defaultMonthFile() {
  if (state.index) {
    const cur = state.index.current;
    if (cur) return cur.endsWith('.json') ? cur : `${cur}.json`;
  }
  return 'current.json';
}

async function loadData() {
  state.index = await loadIndex();
  try {
    state.data = await loadMonthFile(defaultMonthFile());
  } catch (e) {
    // index 指向不存在檔，退回 current.json
    try { state.data = await loadMonthFile('current.json'); }
    catch (e2) { console.error(e2); show('error'); return; }
  }
  state.myName = localStorage.getItem(STORAGE_KEY);
  if (!state.myName || !state.data.people.some(p => p.name === state.myName)) {
    state.myName = null;
    showPicker();
  } else {
    showSchedule();
  }
}

// ── 顯示控制 ───────────────────────────────────────────
function show(id) {
  for (const sec of ['loading', 'error', 'picker', 'schedule']) {
    const el = document.getElementById(sec);
    if (el) el.hidden = sec !== id;
  }
  document.getElementById('topbar').hidden = (id !== 'schedule');
}

// ── 名單選擇 ───────────────────────────────────────────
function showPicker() {
  show('picker');
  const list = document.getElementById('name-list');
  list.innerHTML = '';
  state.data.people.forEach((p, i) => {
    const btn = document.createElement('button');
    btn.className = 'name-btn';
    btn.innerHTML = `<span class="name-num">${i + 1}.</span>${p.name}`;
    btn.onclick = () => {
      state.myName = p.name;
      localStorage.setItem(STORAGE_KEY, p.name);
      showSchedule();
    };
    list.appendChild(btn);
  });
}

// ── 班表顯示 ───────────────────────────────────────────
function showSchedule() {
  show('schedule');
  const d = state.data;
  document.getElementById('title').textContent = `${d.year}年${d.month}月`;
  document.getElementById('footer-text').textContent = `© 達揚連鎖藥局 · ${d.year}/${String(d.month).padStart(2, '0')}`;
  populateMonthSelect();
  populateUserSelect();
  renderCalendar();
}

function populateMonthSelect() {
  const sel = document.getElementById('month-select');
  if (!sel) return;
  sel.innerHTML = '';
  if (!state.index || !Array.isArray(state.index.months) || state.index.months.length === 0) {
    sel.style.display = 'none';
    return;
  }
  sel.style.display = '';
  // 月份新到舊排序
  const months = [...state.index.months].sort((a, b) => (b.year - a.year) || (b.month - a.month));
  for (const m of months) {
    const opt = document.createElement('option');
    opt.value = m.file || `${m.year}-${String(m.month).padStart(2, '0')}.json`;
    opt.textContent = `${m.year}/${String(m.month).padStart(2, '0')}`;
    if (state.data && state.data.year === m.year && state.data.month === m.month) opt.selected = true;
    sel.appendChild(opt);
  }
}

function populateUserSelect() {
  const sel = document.getElementById('user-select');
  sel.innerHTML = '';
  state.data.people.forEach(p => {
    const opt = document.createElement('option');
    opt.value = p.name; opt.textContent = p.name;
    if (p.name === state.myName) opt.selected = true;
    sel.appendChild(opt);
  });
}

function renderCalendar() {
  const d = state.data;
  const total = lastDay(d.year, d.month);
  const myShifts = d.shifts.filter(s => s.name === state.myName);
  const me = d.people.find(p => p.name === state.myName);
  const ghostBlank = me?.ghost_when_blank ?? false;

  // 共班計算（只在 toggle 開時用）
  const coworkers = state.showCoworkers ? computeCoworkers(d.shifts, d.year, d.month) : {};

  // weekday header
  const wdh = document.getElementById('weekday-header');
  wdh.innerHTML = '';
  ['日', '一', '二', '三', '四', '五', '六'].forEach((label, i) => {
    const div = document.createElement('div');
    div.textContent = label;
    if (i === 0 || i === 6) div.classList.add('weekend');
    wdh.appendChild(div);
  });

  // calendar body
  const cal = document.getElementById('calendar');
  cal.innerHTML = '';

  const firstWd = weekdayOf(d.year, d.month, 1);  // 0=Mon

  // 前面補空格
  for (let i = 0; i < firstWd; i++) {
    const cell = document.createElement('div');
    cell.className = 'cell empty';
    cal.appendChild(cell);
  }

  const today = new Date();
  const isCurrentMonth = today.getFullYear() === d.year && (today.getMonth() + 1) === d.month;

  for (let day = 1; day <= total; day++) {
    const cell = document.createElement('div');
    cell.className = 'cell';
    if (isCurrentMonth && today.getDate() === day) cell.classList.add('today');

    const wd = weekdayOf(d.year, d.month, day);
    const dayNum = document.createElement('div');
    dayNum.className = 'day-num';
    if (wd === 0 || wd === 6) dayNum.classList.add('weekend');
    dayNum.textContent = day;
    cell.appendChild(dayNum);

    const myShift = myShifts.find(s => s.day === day);

    if (!myShift) {
      // 空格：依 ghost_when_blank 決定要不要顯示警告
      if (!ghostBlank) {
        const warn = document.createElement('span');
        warn.className = 'warn'; warn.title = '未排班';
        warn.textContent = '?';
        cell.appendChild(warn);
      }
    } else {
      cell.appendChild(makeBadge(myShift));

      // 自訂時段顯示
      if (myShift.hours) {
        const h = document.createElement('div');
        h.className = 'hours';
        const p = parseHours(myShift.hours);
        h.textContent = p ? `${minToHHMM(p[0])}-${minToHHMM(p[1])}` : myShift.hours;
        cell.appendChild(h);
      }

      // 共班
      if (state.showCoworkers && !isOffType(myShift.type) && myShift.loc) {
        const key = `${state.myName}|${day}`;
        const partners = coworkers[key];
        if (myShift.no_coworker_calc || myShift.type === '雙') {
          // 雙頭班不顯示
        } else if (partners && partners.length) {
          const pdiv = document.createElement('div');
          pdiv.className = 'partners';
          pdiv.textContent = partners.join('、');
          cell.appendChild(pdiv);
        } else {
          const adiv = document.createElement('div');
          adiv.className = 'alone';
          adiv.textContent = '獨自';
          cell.appendChild(adiv);
        }
      }
    }
    cal.appendChild(cell);
  }
}

function makeBadge(shift) {
  const b = document.createElement('span');
  if (isOffType(shift.type)) {
    b.className = 'badge off';
    b.textContent = shift.type;
    return b;
  }
  if (shift.type === '雙' && shift.double) {
    // 雙頭班：純 badge，色用 double[0]（先去的場地）
    const main = shift.double[0]?.loc || shift.loc;
    b.className = `badge ${main}`;
    const txt = shift.double.map(d => `${LOC_SHORT[d.loc]}${d.type}`).join('+');
    b.textContent = txt;
    b.title = '雙頭班（總時數 13h）';
    return b;
  }
  b.className = `badge ${shift.loc}`;
  const short = LOC_SHORT[shift.loc] || shift.loc;
  b.textContent = `${short}${shift.type}`;
  return b;
}

// ── 換人下拉 ───────────────────────────────────────────
function setupUserDropdown() {
  document.getElementById('user-select').addEventListener('change', (e) => {
    state.myName = e.target.value;
    localStorage.setItem(STORAGE_KEY, state.myName);
    renderCalendar();
  });
}

// ── 換月下拉 ───────────────────────────────────────────
function setupMonthDropdown() {
  const sel = document.getElementById('month-select');
  if (!sel) return;
  sel.addEventListener('change', async (e) => {
    try {
      state.data = await loadMonthFile(e.target.value);
    } catch (err) {
      console.error(err);
      alert('載入月份失敗');
      return;
    }
    // 該月若沒有當前使用者，就退到名單選擇
    if (state.myName && !state.data.people.some(p => p.name === state.myName)) {
      state.myName = null;
      localStorage.removeItem(STORAGE_KEY);
      showPicker();
      return;
    }
    showSchedule();
  });
}

// ── 共班 toggle ────────────────────────────────────────
function setupCoworkerToggle() {
  document.getElementById('show-coworkers').addEventListener('change', (e) => {
    state.showCoworkers = e.target.checked;
    renderCalendar();
  });
}

// ── 一圖流產圖 ─────────────────────────────────────────
function setupImageButton() {
  document.getElementById('btn-image').addEventListener('click', () => {
    drawScheduleImage();
    document.getElementById('image-output').hidden = false;
    document.getElementById('image-output').scrollIntoView({ behavior: 'smooth' });
  });
  document.getElementById('btn-close-image').addEventListener('click', () => {
    document.getElementById('image-output').hidden = true;
  });
  document.getElementById('btn-download-image').addEventListener('click', downloadScheduleImage);
}

function downloadScheduleImage() {
  const canvas = document.getElementById('schedule-canvas');
  const d = state.data;
  const filename = `${state.myName}_${d.year}${String(d.month).padStart(2, '0')}_班表.png`;
  canvas.toBlob((blob) => {
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, 'image/png');
}

function drawScheduleImage() {
  const d = state.data;
  const total = lastDay(d.year, d.month);
  const myShifts = d.shifts.filter(s => s.name === state.myName);
  const me = d.people.find(p => p.name === state.myName);
  const ghostBlank = me?.ghost_when_blank ?? false;

  // 對齊主流手機物理寬度（iPhone Pro Max 1242 / Pixel 10 Pro 1344），用 1242 同比放大整版
  const W = 1242;
  const TOP_PAD = 50;
  const BOTTOM_PAD = 60;
  const HEADER_H = 200;
  const CELL_H = 230;
  const cellW = W / 7;
  const firstWd = weekdayOf(d.year, d.month, 1);

  const totalSlots = firstWd + total;
  const rows = Math.ceil(totalSlots / 7);
  const H = TOP_PAD + HEADER_H + CELL_H * rows + BOTTOM_PAD;

  const canvas = document.getElementById('schedule-canvas');
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext('2d');

  ctx.fillStyle = '#FFFFFF';
  ctx.fillRect(0, 0, W, H);

  // 標題
  ctx.fillStyle = '#1446A0';
  ctx.font = 'bold 56px "Noto Sans TC", sans-serif';
  ctx.fillText(`達揚連鎖 · ${d.year}年${d.month}月 ${state.myName} 班表`, 36, TOP_PAD + 64);

  // 週標題（週日起）
  ctx.font = 'bold 40px "Noto Sans TC", sans-serif';
  const wdLabels = ['日', '一', '二', '三', '四', '五', '六'];
  for (let i = 0; i < 7; i++) {
    ctx.fillStyle = (i === 0 || i === 6) ? '#d04545' : '#222';
    const lw = ctx.measureText(wdLabels[i]).width;
    ctx.fillText(wdLabels[i], i * cellW + (cellW - lw) / 2, TOP_PAD + 170);
  }

  const calTop = TOP_PAD + HEADER_H;

  // 格子
  for (let day = 1; day <= total; day++) {
    const slot = firstWd + day - 1;
    const col = slot % 7, row = Math.floor(slot / 7);
    const x = col * cellW, y = calTop + row * CELL_H;

    ctx.strokeStyle = '#e7e9ee';
    ctx.lineWidth = 1;
    ctx.strokeRect(x + 0.5, y + 0.5, cellW - 1, CELL_H - 1);

    const wd = weekdayOf(d.year, d.month, day);
    ctx.fillStyle = (wd === 0 || wd === 6) ? '#d04545' : '#666';
    ctx.font = 'bold 34px "Noto Sans TC", sans-serif';
    ctx.fillText(day, x + 14, y + 46);

    const s = myShifts.find(sh => sh.day === day);
    if (!s) {
      if (!ghostBlank) {
        ctx.fillStyle = '#d04545';
        ctx.font = 'bold 30px "Noto Sans TC", sans-serif';
        ctx.fillText('?', x + cellW - 32, y + 46);
      }
      continue;
    }

    const badgeBh = drawBadgeCanvas(ctx, x + 12, y + 70, cellW - 24, s);

    if (s.hours) {
      const p = parseHours(s.hours);
      ctx.fillStyle = '#444';
      ctx.font = '26px "Noto Sans TC", sans-serif';
      const hoursY = y + 70 + badgeBh + 32;
      ctx.fillText(p ? `${minToHHMM(p[0])}-${minToHHMM(p[1])}` : s.hours, x + 14, hoursY);
    }
  }

  // 底部簽名
  ctx.fillStyle = '#888';
  ctx.font = '20px "Noto Sans TC", sans-serif';
  const footer = `© 達揚連鎖藥局 · ${d.year}/${String(d.month).padStart(2, '0')}`;
  const fw = ctx.measureText(footer).width;
  ctx.fillText(footer, (W - fw) / 2, H - 28);
}

function drawBadgeCanvas(ctx, x, y, w, shift) {
  const FS = 64;            // 2 字 ≈ 128px + 20 padding ≈ 148，cellW(177)-24 內幾乎填滿
  const PADX = 10, PADY = 8;
  const LINE_GAP = 6;

  // 計算文字行
  let lines, loc;
  if (isOffType(shift.type)) {
    lines = [shift.type];
    loc = '__off';
  } else if (shift.type === '雙' && shift.double) {
    loc = shift.double[0]?.loc || shift.loc;
    const parts = shift.double.map(d => `${LOC_SHORT[d.loc]}${d.type}`);
    // XX+OO 拆兩行：第一段一行，後續每段帶「+」連在第二行
    lines = [parts[0], parts.slice(1).map(p => '+' + p).join('')];
  } else {
    const short = LOC_SHORT[shift.loc] || shift.loc;
    lines = [`${short}${shift.type}`];
    loc = shift.loc;
  }

  // 字級自動縮放：取最寬一行，若超出 cell 可用寬度則縮字
  let fs = FS;
  ctx.font = `bold ${fs}px "Noto Sans TC", sans-serif`;
  let maxTw = Math.max(...lines.map(l => ctx.measureText(l).width));
  while (maxTw + PADX * 2 > w && fs > 32) {
    fs -= 2;
    ctx.font = `bold ${fs}px "Noto Sans TC", sans-serif`;
    maxTw = Math.max(...lines.map(l => ctx.measureText(l).width));
  }

  const bw = Math.min(w, maxTw + PADX * 2);
  const bh = lines.length * fs + (lines.length - 1) * LINE_GAP + PADY * 2;

  // 底色 + 框
  if (loc === '__off') {
    ctx.fillStyle = '#F4C430';
    ctx.fillRect(x, y, bw, bh);
    ctx.fillStyle = '#C0392B';
  } else if (loc === '日揚') {
    ctx.fillStyle = '#FFFFFF';
    ctx.fillRect(x, y, bw, bh);
    ctx.strokeStyle = '#FF8C00';
    ctx.lineWidth = 2;
    ctx.strokeRect(x + 1, y + 1, bw - 2, bh - 2);
    ctx.fillStyle = '#222';
  } else {
    ctx.fillStyle = LOC_COLOR[loc] || '#888';
    ctx.fillRect(x, y, bw, bh);
    ctx.fillStyle = '#FFFFFF';
  }

  // 文字（baseline 對齊）
  for (let i = 0; i < lines.length; i++) {
    const baseY = y + PADY + (i + 1) * fs - 4 + i * LINE_GAP;
    ctx.fillText(lines[i], x + PADX, baseY);
  }
  return bh;
}

// ── CSV / ICS 下載 ─────────────────────────────────────
function setupCsvButton() {
  document.getElementById('btn-csv').addEventListener('click', downloadCsv);
  document.getElementById('btn-ics').addEventListener('click', downloadIcs);
}

function downloadCsv() {
  const d = state.data;
  const myShifts = d.shifts.filter(s => s.name === state.myName);
  const coworkers = computeCoworkers(d.shifts, d.year, d.month);

  const header = ['Subject', 'Start Date', 'Start Time', 'End Date', 'End Time',
                  'All Day Event', 'Description', 'Location', 'Private'];
  const byStore = { '達揚': [header.slice()], '日揚': [header.slice()], '健揚': [header.slice()] };

  for (const s of myShifts) {
    if (isOffType(s.type) || !s.type) continue;
    if (!s.loc || !byStore[s.loc]) continue;
    const wd = weekdayOf(d.year, d.month, s.day);
    const win = getShiftWindow(s.name, s.loc, s.type, s.hours, wd);
    if (!win) continue;

    const dateFmt = `${String(d.month).padStart(2, '0')}/${String(s.day).padStart(2, '0')}/${d.year}`;
    const subject = s.type === '雙'
      ? '雙頭班'
      : `${LOC_SHORT[s.loc] || s.loc}${s.type}`;
    const cw = coworkers[`${s.name}|${s.day}`] || [];
    const desc = cw.length ? `共班：${cw.join('、')}` : '';
    const locName = LOC_NAME_MAP[s.loc] || s.loc || '';

    byStore[s.loc].push([
      subject, dateFmt, minTo12h(win[0]),
      dateFmt, minTo12h(win[1]),
      'FALSE', desc, locName, 'FALSE'
    ]);
  }

  const ymd = `${d.year}${String(d.month).padStart(2, '0')}`;
  const stores = ['達揚', '日揚', '健揚'];
  let delay = 0;
  for (const store of stores) {
    if (byStore[store].length <= 1) continue;  // 該店無班次就跳過
    const csv = byStore[store].map(r => r.map(escapeCsv).join(',')).join('\r\n');
    const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' });
    const filename = `${state.myName}_${ymd}_${store}.csv`;
    setTimeout(() => triggerDownload(blob, filename), delay);
    delay += 400;  // 部分瀏覽器需間隔避免被擋
  }
}

function downloadIcs() {
  const d = state.data;
  const myShifts = d.shifts.filter(s => s.name === state.myName);
  const coworkers = computeCoworkers(d.shifts, d.year, d.month);

  const lines = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//Dayang//Schedule//ZH-TW',
    'CALSCALE:GREGORIAN',
    'METHOD:PUBLISH',
    `X-WR-CALNAME:${state.myName} ${d.year}/${String(d.month).padStart(2,'0')} 班表`,
    'X-WR-TIMEZONE:Asia/Taipei',
  ];

  const dtstamp = formatIcsLocal(new Date());

  for (const s of myShifts) {
    if (isOffType(s.type) || !s.type) continue;
    if (!s.loc) continue;
    const wd = weekdayOf(d.year, d.month, s.day);
    const win = getShiftWindow(s.name, s.loc, s.type, s.hours, wd);
    if (!win) continue;

    const dtstart = formatIcsTime(d.year, d.month, s.day, win[0]);
    const dtend = formatIcsTime(d.year, d.month, s.day, win[1]);
    const subject = s.type === '雙' ? '雙頭班' : `${LOC_SHORT[s.loc] || s.loc}${s.type}`;
    const cw = coworkers[`${s.name}|${s.day}`] || [];
    const desc = cw.length ? `共班：${cw.join('、')}` : '';
    const locName = LOC_NAME_MAP[s.loc] || s.loc || '';
    const uid = `${s.name}-${d.year}${String(d.month).padStart(2,'0')}${String(s.day).padStart(2,'0')}-${subject}@dayang`;

    lines.push(
      'BEGIN:VEVENT',
      `UID:${uid}`,
      `DTSTAMP:${dtstamp}`,
      `DTSTART;TZID=Asia/Taipei:${dtstart}`,
      `DTEND;TZID=Asia/Taipei:${dtend}`,
      `SUMMARY:${icsEscape(subject)}`,
      `LOCATION:${icsEscape(locName)}`,
      `DESCRIPTION:${icsEscape(desc)}`,
      'END:VEVENT'
    );
  }

  lines.push('END:VCALENDAR');
  const ics = lines.join('\r\n');
  const blob = new Blob([ics], { type: 'text/calendar;charset=utf-8' });
  const ymd = `${d.year}${String(d.month).padStart(2,'0')}`;
  triggerDownload(blob, `${state.myName}_${ymd}_班表.ics`);
}

function formatIcsTime(year, month, day, min) {
  const h = Math.floor(min / 60), m = min % 60;
  return `${year}${String(month).padStart(2,'0')}${String(day).padStart(2,'0')}T${String(h).padStart(2,'0')}${String(m).padStart(2,'0')}00`;
}

function formatIcsLocal(date) {
  const y = date.getFullYear();
  const mo = String(date.getMonth()+1).padStart(2,'0');
  const da = String(date.getDate()).padStart(2,'0');
  const h = String(date.getHours()).padStart(2,'0');
  const mi = String(date.getMinutes()).padStart(2,'0');
  const s = String(date.getSeconds()).padStart(2,'0');
  return `${y}${mo}${da}T${h}${mi}${s}`;
}

function icsEscape(s) {
  return String(s || '').replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\n/g, '\\n');
}

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function escapeCsv(s) {
  s = String(s);
  if (s.includes(',') || s.includes('"') || s.includes('\n')) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

// ── Init ───────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  setupUserDropdown();
  setupMonthDropdown();
  setupCoworkerToggle();
  setupImageButton();
  setupCsvButton();
  loadData();
});
