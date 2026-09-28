import { connect, api } from "/static/common.js";

const stage = document.getElementById("stage");
const table = document.getElementById("table");
const overlay = document.getElementById("overlay");
const tpl = document.getElementById("tile-tpl");
const tiles = new Map(); // player_id -> {seat, tile, els, lastLevel, beatBpm, frac, ...}
let layoutKey = "";
let latest = null;
let geo = null; // current track + tile geometry, for dragging and alerts
let drag = null;
let turn = 0; // degrees the whole picture is turned, to match how the monitor is mounted

// ---------- layout ----------

// The track tiles sit on: a rounded rectangle around the edge of the screen,
// measured from bottom centre running clockwise (the order cards are dealt).
// A seat's place on it is a fraction 0..1 of the way round.
function buildTrack(hw, hh, r) {
  r = Math.min(r, hw, hh);
  const pts = [[0, hh]];
  const arc = (cx, cy, a0) => {
    for (let k = 0; k <= 24; k++) {
      const a = a0 + (k / 24) * (Math.PI / 2);
      pts.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
    }
  };
  arc(-hw + r, hh - r, Math.PI / 2); // bottom-left
  arc(-hw + r, -hh + r, Math.PI); // top-left
  arc(hw - r, -hh + r, 1.5 * Math.PI); // top-right
  arc(hw - r, hh - r, 0); // bottom-right
  pts.push([0, hh]);
  const cum = [0];
  for (let k = 1; k < pts.length; k++) {
    cum.push(cum[k - 1] + Math.hypot(pts[k][0] - pts[k - 1][0], pts[k][1] - pts[k - 1][1]));
  }
  return { pts, cum, total: cum.at(-1) };
}

const wrap = (f) => ((f % 1) + 1) % 1;

// Point at fraction f, plus the rotation that makes a tile's bottom edge face
// outward, toward the person sitting on that side of the table.
function trackAt(track, f) {
  const { pts, cum, total } = track;
  const target = wrap(f) * total;
  let k = 0;
  while (k < pts.length - 2 && cum[k + 1] < target) k++;
  const [x0, y0] = pts[k], [x1, y1] = pts[k + 1];
  const len = Math.hypot(x1 - x0, y1 - y0) || 1;
  const t = (target - cum[k]) / len;
  const tx = (x1 - x0) / len, ty = (y1 - y0) / len;
  // outward normal of a clockwise (on screen) path is the tangent turned right
  const rot = (Math.atan2(-tx, ty) * 180) / Math.PI - 90;
  return { x: x0 + t * (x1 - x0), y: y0 + t * (y1 - y0), rot };
}

// Fraction of the nearest point on the track to (x, y), centre-relative.
function trackNearest(track, x, y) {
  const { pts, cum, total } = track;
  let best = Infinity, bestF = 0;
  for (let k = 0; k < pts.length - 1; k++) {
    const [x0, y0] = pts[k], [x1, y1] = pts[k + 1];
    const dx = x1 - x0, dy = y1 - y0, l2 = dx * dx + dy * dy || 1;
    const t = Math.max(0, Math.min(1, ((x - x0) * dx + (y - y0) * dy) / l2));
    const d = Math.hypot(x0 + t * dx - x, y0 + t * dy - y);
    if (d < best) { best = d; bestF = (cum[k] + t * Math.sqrt(l2)) / total; }
  }
  return wrap(bestF);
}

// Where each seat goes: its saved spot if it has one, otherwise evenly spaced
// by seat order. Players added after tiles were dragged go into the biggest gap.
function seatFractions(seats) {
  const n = seats.length;
  if (!seats.some((s) => s.pos != null)) return seats.map((_, i) => i / n);
  const fr = seats.map((s) => s.pos);
  fr.forEach((f, i) => {
    if (f != null) return;
    const used = fr.filter((x) => x != null).sort((a, b) => a - b);
    let spot = 0, gap = -1;
    used.forEach((x, k) => {
      const next = k + 1 < used.length ? used[k + 1] : used[0] + 1;
      if (next - x > gap) { gap = next - x; spot = x + gap / 2; }
    });
    fr[i] = wrap(spot);
  });
  return fr;
}

function placeSeat(seat, f) {
  const { track, W, H, tileW, tileH, m } = geo;
  const p = trackAt(track, f);
  // keep the whole rotated tile on screen
  const a = (p.rot * Math.PI) / 180, c = Math.abs(Math.cos(a)), sn = Math.abs(Math.sin(a));
  const ex = (tileW / 2) * c + (tileH / 2) * sn, ey = (tileW / 2) * sn + (tileH / 2) * c;
  const x = Math.max(-W / 2 + ex + m, Math.min(W / 2 - ex - m, p.x));
  const y = Math.max(-H / 2 + ey + m, Math.min(H / 2 - ey - m, p.y));
  seat.style.left = `${W / 2 + x}px`;
  seat.style.top = `${H / 2 + y}px`;
  seat.style.transform = `rotate(${p.rot}deg)`;
  return { x, y, rot: p.rot };
}

function applyLayout(state) {
  const n = state.seats.length;
  const { layout, rotation } = state.settings;
  turn = (((state.settings.turn || 0) % 360) + 360) % 360;
  const sideways = turn === 90 || turn === 270;
  // W and H are the picture's own width and height, before it's turned
  const W = sideways ? innerHeight : innerWidth, H = sideways ? innerWidth : innerHeight;
  const key = JSON.stringify([W, H, layout, rotation, turn, state.seats.map((s) => [s.player_id, s.pos])]);
  if (key === layoutKey || drag) return;
  layoutKey = key;
  stage.style.width = `${W}px`;
  stage.style.height = `${H}px`;
  stage.style.setProperty("--turn", `${turn}deg`);
  document.body.dataset.layout = layout;
  document.body.classList.toggle("grid", layout === "grid");

  if (layout === "grid") {
    const cols = n <= 4 ? Math.max(n, 1) : n <= 8 ? Math.ceil(n / 2) : Math.ceil(n / 3);
    const rows = Math.ceil(n / cols) || 1;
    // u is the size unit every tile dimension hangs off (the BPM number is 0.38u)
    const u = Math.min(((W * 0.92) / cols - 20) / 1.7, ((H * 0.9) / rows - 20) / 0.7);
    table.style.setProperty("--cols", cols);
    document.documentElement.style.setProperty("--u", `${u}px`);
    geo = null;
  } else if (layout === "end") {
    layoutEnd(state.seats, W, H);
  } else {
    layoutAround(state.seats, W, H, rotation);
  }
}

// Screen in the middle of the table: tiles around the edge, each facing out.
function layoutAround(seats, W, H, rotation) {
  const n = seats.length;
  // sized for 7 around a 16:9 screen; smaller when more
  const u = Math.min(W, (H * 16) / 9) * 0.15 * (n <= 7 ? 1 : Math.sqrt(7 / n));
  const tileW = u * 1.7, tileH = u * 0.7;
  document.documentElement.style.setProperty("--u", `${u}px`);
  const m = Math.min(W, H) * 0.015;
  const track = buildTrack(W / 2 - tileH / 2 - m, H / 2 - tileH / 2 - m, tileW * 0.5);
  geo = { mode: "table", track, W, H, u, tileW, tileH, m, offset: (rotation || 0) / 360 };
  const fr = seatFractions(seats);
  seats.forEach((s, i) => {
    const t = tiles.get(s.player_id);
    t.frac = wrap(fr[i] + geo.offset);
    t.place = placeSeat(t.seat, t.frac);
  });
}

// Screen standing at one end of the table, everyone looking at it: a map of
// the table from above, with the screen's end at the top. Two columns for
// the long sides, and the far end at the bottom when the count is odd.
// Seat order runs clockwise from the far end, as in the other layout.
function layoutEnd(seats, W, H) {
  const n = seats.length;
  const bottom = n % 2, side = (n - bottom) / 2, rows = Math.max(side + bottom, 1);
  const gap = Math.min(W, H) * 0.03;
  const band = H * 0.22; // kept clear at the top (the screen's end) for notifications
  const avail = H - band;
  const u = Math.min((W - 3 * gap) / (2 * 1.7), (avail - (rows + 1) * gap) / (rows * 0.7), Math.min(W, H) * 0.3);
  const tileW = u * 1.7, tileH = u * 0.7;
  document.documentElement.style.setProperty("--u", `${u}px`);
  geo = { mode: "end", W, H, u, tileW, tileH, m: gap, band };

  const pitch = rows > 1 ? (avail - 2 * gap - tileH) / (rows - 1) : 0;
  const rowY = (r) => band + (rows > 1 ? gap + tileH / 2 + r * pitch : avail / 2);
  const leftX = gap + tileW / 2, rightX = W - gap - tileW / 2;
  const spots = [];
  if (bottom) spots.push([W / 2, rowY(rows - 1)]);
  for (let r = side - 1; r >= 0; r--) spots.push([leftX, rowY(r)]); // left side, far end first
  for (let r = 0; r < side; r++) spots.push([rightX, rowY(r)]); // right side, screen end first
  seats.forEach((s, i) => {
    const t = tiles.get(s.player_id);
    const [x, y] = spots[i];
    t.seat.style.left = `${x}px`;
    t.seat.style.top = `${y}px`;
    t.seat.style.transform = "none";
    t.place = { x: x - W / 2, y: y - H / 2, rot: 0 };
  });
}

// ---------- dragging tiles on the display ----------
// Around the table, tiles slide along the edge. In either layout, dropping a
// tile on another swaps them.

// pointer position in the picture's own coordinates (centre-relative), undoing the turn
function toStage(e) {
  const dx = e.clientX - innerWidth / 2, dy = e.clientY - innerHeight / 2;
  const a = (-turn * Math.PI) / 180;
  return [dx * Math.cos(a) - dy * Math.sin(a), dx * Math.sin(a) + dy * Math.cos(a)];
}

table.addEventListener("pointerdown", (e) => {
  const seat = e.target.closest(".seat");
  if (!seat || !geo) return;
  e.preventDefault();
  seat.setPointerCapture(e.pointerId);
  seat.classList.add("dragging");
  const id = seat.dataset.id, t = tiles.get(id);
  drag = { id, seat, start: toStage(e), startFrac: t.frac, frac: t.frac, moved: false };
});

table.addEventListener("pointermove", (e) => {
  if (!drag) return;
  const [x, y] = toStage(e);
  drag.moved = true;
  if (geo.mode === "table") {
    drag.frac = trackNearest(geo.track, x, y);
    placeSeat(drag.seat, drag.frac);
  } else {
    drag.at = [tiles.get(drag.id).place.x + x - drag.start[0], tiles.get(drag.id).place.y + y - drag.start[1]];
    drag.seat.style.transform = `translate(${x - drag.start[0]}px, ${y - drag.start[1]}px)`;
  }
  const target = dropTarget();
  for (const [id, t] of tiles) t.seat.classList.toggle("drop-target", id === target);
});

function dropTarget() {
  for (const [id, t] of tiles) {
    if (id === drag.id) continue;
    if (geo.mode === "table") {
      // another tile whose spot is within roughly half a tile of where we are
      const d = Math.abs(wrap(t.frac - drag.frac + 0.5) - 0.5);
      if (d < (geo.tileW * 0.55) / geo.track.total) return id;
    } else if (drag.at) {
      const [x, y] = drag.at;
      if (Math.abs(t.place.x - x) < geo.tileW / 2 && Math.abs(t.place.y - y) < geo.tileH / 2) return id;
    }
  }
  return null;
}

async function endDrag() {
  if (!drag) return;
  const { id, seat, startFrac, frac, moved } = drag;
  const target = moved ? dropTarget() : null;
  seat.classList.remove("dragging");
  for (const t of tiles.values()) t.seat.classList.remove("drop-target");
  const mode = geo.mode;
  drag = null;
  layoutKey = "";
  if (!moved) return;
  if (mode === "end") {
    if (target) {
      const order = latest.seats.map((s) => s.player_id);
      const i = order.indexOf(id), j = order.indexOf(target);
      [order[i], order[j]] = [order[j], order[i]];
      await api("POST", "/api/seats", { order });
    } else if (latest) {
      render(latest); // snap back
    }
    return;
  }
  tiles.get(id).frac = frac;
  if (target) {
    tiles.get(id).frac = tiles.get(target).frac;
    tiles.get(target).frac = startFrac;
  }
  const positions = {};
  for (const [pid, t] of tiles) positions[pid] = wrap(t.frac - geo.offset);
  await api("POST", "/api/layout", { positions });
}
table.addEventListener("pointerup", endDrag);
table.addEventListener("pointercancel", endDrag);

// ---------- tiles ----------

function makeTile(playerId) {
  const seat = tpl.content.firstElementChild.cloneNode(true);
  seat.dataset.id = playerId;
  const q = (c) => seat.querySelector(c);
  const t = {
    seat, tile: q(".tile"), lastLevel: 0, beatBpm: 0,
    els: { name: q(".name"), bpm: q(".bpm"), delta: q(".delta"), heart: q(".heart"), noise: q(".noise") },
  };
  tiles.set(playerId, t);
  return t;
}

function syncTiles(seats) {
  const ids = seats.map((s) => s.player_id);
  for (const [id, t] of tiles) {
    if (!ids.includes(id)) { t.seat.remove(); tiles.delete(id); }
  }
  seats.forEach((s, i) => {
    const t = tiles.get(s.player_id) || makeTile(s.player_id);
    // DOM order = seat order (matters for grid layout); only move when it changed
    if (table.children[i] !== t.seat) table.insertBefore(t.seat, table.children[i] || null);
  });
  let empty = document.getElementById("empty");
  if (!seats.length && !empty) {
    empty = document.createElement("div");
    empty.id = "empty";
    empty.innerHTML = `No players seated yet.<br>Open <a href="/setup">/setup</a> on the laptop to add them.`;
    document.body.appendChild(empty);
  } else if (seats.length && empty) {
    empty.remove();
  }
}

const MESSAGES = {
  stale: "No signal",
  waiting: "Waiting",
  nodevice: "No device",
};

function renderTile(s) {
  const t = tiles.get(s.player_id);
  const { els, tile } = t;
  const live = s.status === "live";
  const hasData = live || s.status === "stale";
  const level = live && !s.calibrating ? s.level : 0;
  const i = level ? s.intensity : 0;

  tile.dataset.status = s.status;
  tile.dataset.level = level;
  tile.style.setProperty("--i", i);
  // shaking and static only when tilting
  const u = geo?.u ?? parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--u"));
  tile.style.setProperty("--amp", level === 2 ? `${(i * u * 0.03).toFixed(1)}px` : "0px");
  els.noise.style.opacity = level === 2 ? Math.max(0, (i - 0.6) / 0.4) * 0.35 : 0;
  if (level === 2 && t.lastLevel < 2) {
    tile.classList.remove("flash");
    void tile.offsetWidth;
    tile.classList.add("flash");
  }
  t.lastLevel = level;

  els.name.textContent = s.name;
  els.bpm.textContent = hasData ? s.bpm : "--";
  if (!live) {
    setDelta(els.delta, MESSAGES[s.status], true);
  } else if (s.calibrating) {
    setDelta(els.delta, "learning", true);
  } else {
    const d = Math.round(s.delta);
    setDelta(els.delta, `${d > 0 ? "+" : d < 0 ? "−" : "±"}${Math.abs(d)}`, false);
  }
  if (live && Math.abs(s.bpm - t.beatBpm) >= 3) {
    t.beatBpm = s.bpm;
    els.heart.style.animationDuration = `${(60 / s.bpm).toFixed(3)}s`;
  }
}

function setDelta(el, text, note) {
  el.textContent = text;
  el.classList.toggle("note", note);
}

// ---------- notifications ----------
// Tilt alerts come from the server as events. They play
// one at a time in the middle, shown twice back to back so both sides of the
// table can read them.

const DURATION_MS = { tilt: 5000 };
const queue = [];
let lastEventId = null;
let playing = false;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const esc = (x) => String(x).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

function takeEvents(events) {
  const newest = events.length ? events.at(-1).id : 0;
  // first message, or the server restarted: don't replay old events
  if (lastEventId === null || newest < lastEventId) {
    lastEventId = newest;
    return;
  }
  for (const e of events) {
    if (e.id > lastEventId && e.type === "tilt") queue.push({ ...e, arrived: Date.now() });
  }
  lastEventId = newest;
  if (!playing) play();
}

async function play() {
  playing = true;
  while (queue.length) {
    const e = queue.shift();
    // a tilt alert that waited behind others for too long is old news
    if (e.type === "tilt" && Date.now() - e.arrived > 20000) continue;
    showNote(e);
    await sleep(DURATION_MS[e.type] ?? 5000);
    overlay.querySelectorAll(".note-card").forEach((c) => c.classList.add("out"));
    document.body.classList.remove("recap");
    await sleep(600);
    overlay.innerHTML = "";
  }
  playing = false;
}

function showNote(e) {
  // lying flat in the middle: two copies back to back in the open middle.
  // Standing at the end: one upright copy in the clear band at the top.
  const flat = geo?.mode === "table";
  const inBand = geo?.mode === "end";
  document.body.classList.toggle("recap", !flat && !inBand);
  overlay.classList.toggle("single", !flat);
  const W = parseFloat(stage.style.width) || innerWidth, H = parseFloat(stage.style.height) || innerHeight;
  const inset = flat ? geo.tileH + 2 * geo.m : 0;
  overlay.style.inset = inBand ? `0 0 ${H - geo.band}px 0` : `${inset}px`;
  const w = W - 2 * inset;
  const room = inBand ? geo.band * 1.1 : (H - 2 * inset) / (flat ? 2 : 1.6);
  const sub = `on tilt · +${e.delta}`;
  const big = Math.min(room * 0.55, (w * 0.85) / (Math.max(e.name.length, 4) * 0.62), (w * 0.85) / (sub.length * 0.36 * 0.8));
  overlay.style.setProperty("--big", `${big}px`);
  const html = `<div><div class="big">${esc(e.name)}</div><div class="small">${esc(sub)}</div></div>`;
  overlay.innerHTML = flat
    ? `<div class="note-card flip tilt">${html}</div><div class="note-card tilt">${html}</div>`
    : `<div class="note-card tilt">${html}</div>`;
}

function render(state) {
  latest = state;
  syncTiles(state.seats);
  applyLayout(state);
  state.seats.forEach(renderTile);
  takeEvents(state.events || []);
}

connect(render, (ok) => document.body.classList.toggle("offline", !ok));
addEventListener("resize", () => { layoutKey = ""; if (latest) render(latest); });

// ---------- full screen, wake lock, idle cursor ----------

let wakeLock = null;
document.addEventListener("click", async () => {
  if (!document.fullscreenElement) await document.documentElement.requestFullscreen().catch(() => {});
  try { wakeLock = await navigator.wakeLock?.request("screen"); } catch {}
  document.body.classList.add("hint-gone");
});
document.addEventListener("visibilitychange", async () => {
  if (wakeLock && document.visibilityState === "visible") {
    try { wakeLock = await navigator.wakeLock.request("screen"); } catch {}
  }
});
let idleTimer;
document.addEventListener("mousemove", () => {
  document.body.classList.remove("idle");
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => document.body.classList.add("idle"), 2500);
});
setTimeout(() => document.body.classList.add("hint-gone"), 8000);
