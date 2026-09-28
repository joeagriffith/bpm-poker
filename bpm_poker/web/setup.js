import { api, connect, fmtClock } from "/static/common.js";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

let state = null;
let structureKey = "";
let dragging = null;

// ---------- live status helpers ----------

function deviceStatus(dev) {
  if (!dev) return { cls: "", text: "not found" };
  if (dev.live) return { cls: "live", text: "live" };
  if (dev.connected) return { cls: "connected", text: "connected, no reading · is it on?" };
  if (dev.last_sample) return { cls: "lost", text: `lost ${Math.round(dev.age)}s ago` };
  if (!dev.last_seen) return { cls: "", text: "off · put it on" };
  return { cls: "", text: "seen, connecting…" };
}

function bpmChip(dev) {
  const st = deviceStatus(dev);
  const bpm = dev?.live ? `<b>${dev.bpm}</b> bpm` : "";
  return `<span class="dot ${st.cls}"></span>${bpm || `<span>${esc(dev ? st.text : "—")}</span>`}`;
}

// ---------- rendering ----------

function deviceOptions(player) {
  const opts = [`<option value="">— no device —</option>`];
  for (const d of state.devices) {
    const other = d.assigned_to && d.id !== player.device_id ? ` (${d.assigned_to})` : "";
    const sel = d.id === player.device_id ? " selected" : "";
    opts.push(`<option value="${esc(d.id)}"${sel}>${esc(d.label)}${esc(other)}</option>`);
  }
  if (player.device_id && !state.devices.some((d) => d.id === player.device_id)) {
    opts.push(`<option value="${esc(player.device_id)}" selected>Last used device (not seen yet)</option>`);
  }
  return opts.join("");
}

function renderStructure() {
  const seated = state.players.filter((p) => p.seated);
  const bench = state.players.filter((p) => !p.seated);

  $("#seated").innerHTML = seated.length ? seated.map((p, i) => `
    <li draggable="true" data-id="${p.id}">
      <span class="handle" title="Drag to reorder">⠿</span>
      <span class="seatno">${i + 1}</span>
      <input class="pname" value="${esc(p.name)}" data-id="${p.id}" aria-label="Name">
      <select class="pdev" data-id="${p.id}" aria-label="Device">${deviceOptions(p)}</select>
      <span class="chip" data-chip="${esc(p.device_id || "")}"></span>
      <span class="actions">
        <button class="ghost" data-move="-1" data-id="${p.id}" title="Move up">↑</button>
        <button class="ghost" data-move="1" data-id="${p.id}" title="Move down">↓</button>
        <button class="ghost" data-stand="${p.id}" title="Not playing tonight">Sit out</button>
      </span>
    </li>`).join("") : `<li class="empty">Add players above, or bring them in from the list below.</li>`;

  $("#bench").innerHTML = bench.length ? bench.map((p) => `
    <li data-id="${p.id}">
      <span>${esc(p.name)}</span>
      <span class="actions">
        <button data-sit="${p.id}">Deal in</button>
        <button class="ghost" data-remove="${p.id}" title="Forget this player">Remove</button>
      </span>
    </li>`).join("") : `<li class="empty">Everyone's at the table.</li>`;

  $("#devices").innerHTML = state.devices.length ? state.devices.map((d) => `
    <li data-dev="${esc(d.id)}" class="${d.assigned_to ? "" : "free"}">
      <span class="dot" data-devdot="${esc(d.id)}"></span>
      <input class="alias" data-alias="${esc(d.id)}" value="${esc(d.alias)}" placeholder="${esc(d.kind_label + " · " + d.name)}" title="Give it a name you'll recognise, e.g. the number on its sticker">
      <span class="chip" data-devbpm="${esc(d.id)}"></span>
      <span class="meta">
        ${esc(d.kind_label)} · ${esc(d.name)}
        ${d.assigned_to ? ` · <span class="who">worn by ${esc(d.assigned_to)}</span>` : " · unassigned"}
        <span data-devmeta="${esc(d.id)}"></span>
      </span>
    </li>`).join("") : `<li class="empty">Scanning… no heart-rate devices found yet.</li>`;
}

function renderLive() {
  $("#clock").textContent = fmtClock(state.game.elapsed);
  const byId = Object.fromEntries(state.devices.map((d) => [d.id, d]));
  document.querySelectorAll("[data-chip]").forEach((el) => {
    const id = el.dataset.chip;
    el.innerHTML = id ? bpmChip(byId[id]) : `<span class="dot"></span><span>—</span>`;
  });
  for (const d of state.devices) {
    const dot = document.querySelector(`[data-devdot="${CSS.escape(d.id)}"]`);
    if (!dot) continue;
    const st = deviceStatus(d);
    dot.className = `dot ${st.cls}`;
    document.querySelector(`[data-devbpm="${CSS.escape(d.id)}"]`).innerHTML = d.live ? `<b>${d.bpm}</b> bpm` : "";
    const bits = [];
    if (!d.live) bits.push(st.text);
    if (d.battery != null) bits.push(`battery ${d.battery}%`);
    if (d.rssi != null) bits.push(`signal ${d.rssi} dBm`);
    const meta = document.querySelector(`[data-devmeta="${CSS.escape(d.id)}"]`);
    meta.innerHTML = (bits.length ? " · " + esc(bits.join(" · ")) : "") +
      (d.error && !d.live ? ` · <span class="err">${esc(d.error)}</span>` : "");
  }

  const s = state.settings;
  document.querySelectorAll("#layout button").forEach((b) => b.classList.toggle("on", b.dataset.v === s.layout));
  document.querySelectorAll("#sensitivity button").forEach((b) =>
    b.classList.toggle("on", Math.abs(parseFloat(b.dataset.v) - s.sensitivity) < 0.01));
}

function render(next) {
  state = next;
  const key = JSON.stringify([
    state.players,
    state.devices.map((d) => [d.id, d.label, d.alias, d.assigned_to]),
  ]);
  const busy = dragging || document.activeElement?.matches("#seated *, #bench *, #devices *");
  if (key !== structureKey && !busy) {
    structureKey = key;
    renderStructure();
  }
  renderLive();
}

// ---------- actions ----------

const refresh = async () => render(await api("GET", "/api/state"));
const act = async (...args) => { await api(...args); structureKey = ""; await refresh(); };

$("#add-player").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = $("#new-name").value.trim();
  if (!name) return;
  $("#new-name").value = "";
  await act("POST", "/api/players", { name });
});

document.addEventListener("change", async (e) => {
  const t = e.target;
  if (t.matches(".pdev")) {
    const body = t.value ? { device_id: t.value } : { clear_device: true };
    await act("PATCH", `/api/players/${t.dataset.id}`, body);
  } else if (t.matches(".pname")) {
    if (t.value.trim()) await act("PATCH", `/api/players/${t.dataset.id}`, { name: t.value.trim() });
  } else if (t.matches(".alias")) {
    await act("PATCH", `/api/devices/${encodeURIComponent(t.dataset.alias)}`, { alias: t.value });
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.matches(".pname, .alias")) e.target.blur();
});

const seatedIds = () => state.players.filter((p) => p.seated).map((p) => p.id);

document.addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  const d = b.dataset;
  if (d.move) {
    const ids = seatedIds(), i = ids.indexOf(d.id), j = i + Number(d.move);
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    await act("POST", "/api/seats", { order: ids });
  } else if (d.stand) {
    await act("PATCH", `/api/players/${d.stand}`, { seated: false });
  } else if (d.sit) {
    await act("PATCH", `/api/players/${d.sit}`, { seated: true });
    await act("POST", "/api/seats", { order: [...seatedIds().filter((x) => x !== d.sit), d.sit] });
  } else if (d.remove) {
    const p = state.players.find((x) => x.id === d.remove);
    if (confirm(`Forget ${p.name}?`)) await act("DELETE", `/api/players/${d.remove}`);
  } else if (b.parentElement.id === "layout") {
    await act("PATCH", "/api/settings", { layout: d.v });
  } else if (b.parentElement.id === "sensitivity") {
    await act("PATCH", "/api/settings", { sensitivity: parseFloat(d.v) });
  } else if (b.id === "rot-left" || b.id === "rot-right") {
    const n = Math.max(seatedIds().length, 1);
    const step = (360 / n) * (b.id === "rot-right" ? 1 : -1);
    await act("PATCH", "/api/settings", { rotation: ((state.settings.rotation || 0) + step + 360) % 360 });
  } else if (b.id === "turn" || b.id === "flip") {
    const step = b.id === "turn" ? 90 : 180;
    await act("PATCH", "/api/settings", { turn: ((state.settings.turn || 0) + step) % 360 });
  } else if (b.id === "new-game") {
    if (confirm("Start a new game? Everyone's baseline and the clock reset.")) await act("POST", "/api/game/new");
  }
});

// drag to reorder seats
const list = $("#seated");
list.addEventListener("dragstart", (e) => {
  dragging = e.target.closest("li");
  dragging.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move";
});
list.addEventListener("dragover", (e) => {
  if (!dragging) return;
  e.preventDefault();
  const over = e.target.closest("li");
  if (!over || over === dragging) return;
  const r = over.getBoundingClientRect();
  list.insertBefore(dragging, e.clientY > r.top + r.height / 2 ? over.nextSibling : over);
});
list.addEventListener("dragend", async () => {
  dragging.classList.remove("dragging");
  dragging = null;
  const order = [...list.querySelectorAll("li[data-id]")].map((li) => li.dataset.id);
  await act("POST", "/api/seats", { order });
});

connect(render, (ok) => document.body.classList.toggle("offline", !ok));
