// Shared by the display and setup screens.

// Keep in sync with the .tile[data-level] colours in display.css.
export const LEVELS = [
  { key: "cool", label: "Cool", color: "#22d3ee" },
  { key: "warm", label: "Warm", color: "#facc15" },
  { key: "tilt", label: "Tilt", color: "#ff3d7f" },
];

// Live state over a WebSocket that reconnects on its own.
export function connect(onState, onStatus = () => {}) {
  let ws;
  const open = () => {
    ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onopen = () => onStatus(true);
    ws.onmessage = (e) => onState(JSON.parse(e.data));
    ws.onclose = () => { onStatus(false); setTimeout(open, 1000); };
  };
  open();
}

export async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "content-type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${method} ${path}: ${res.status}`);
  return res.json();
}

export function fmtClock(sec) {
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const mm = String(m).padStart(h ? 2 : 1, "0"), ss = String(s).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}
