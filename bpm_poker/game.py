"""Joins devices to players: routes samples to per-player trackers and builds
the snapshot that the display and setup screens render."""

from __future__ import annotations

import csv
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from .hub import Device, Hub
from .store import Store
from .tracker import SpikeTracker

TILT_ALERT_COOLDOWN = 60.0  # s: at most one tilt alert per player per minute

KIND_LABELS = {
    "whoop": "Whoop",
    "coospo": "Coospo",
    "fitbit": "Fitbit",
    "generic": "HR sensor",
    "sim": "Simulated",
    "push": "Push",
}


class Game:
    def __init__(self, hub: Hub, store: Store, log_dir: Path) -> None:
        self.hub = hub
        self.store = store
        self.log_dir = log_dir
        self.trackers: dict[str, SpikeTracker] = {}
        self._log_file = None
        self._log = None
        self._night_path = None
        self._night_file = None
        # events the display turns into big notifications
        self.events: deque[dict] = deque(maxlen=20)
        self._event_id = 0
        self._prev_level: dict[str, int] = {}
        self._last_tilt_alert: dict[str, float] = {}
        hub.on_sample(self._on_sample)
        self.new_game()

    def new_game(self) -> None:
        self.started_at = time.time()
        self.trackers.clear()
        self._prev_level.clear()
        if self._log_file:
            self._log_file.close()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / f"{datetime.now():%Y-%m-%d_%H%M%S}.csv"
        self._log_file = path.open("w", newline="")
        self._log = csv.writer(self._log_file)
        self._log.writerow(["time", "player", "device", "bpm"])

    def reset_player(self, player_id: str) -> None:
        self.trackers.pop(player_id, None)

    def _on_sample(self, device_id: str, bpm: int, t: float) -> None:
        player = next((p for p in self.store.players if p["device_id"] == device_id and p["seated"]), None)
        self._log_night(t, player["name"] if player else "", device_id, bpm)
        if player:
            self.trackers.setdefault(player["id"], SpikeTracker()).add(t, bpm)
            self._log.writerow([round(t, 2), player["name"], device_id, bpm])
            self._log_file.flush()

    def _log_night(self, t: float, name: str, device_id: str, bpm: int) -> None:
        """Every reading from every device, appended to one file per night
        (a night runs 6am to 6am, so a game past midnight stays in one file)."""
        path = self.log_dir.parent / "nights" / f"{datetime.fromtimestamp(t - 6 * 3600):%Y-%m-%d}.csv"
        if path != self._night_path:
            if self._night_file:
                self._night_file.close()
            path.parent.mkdir(parents=True, exist_ok=True)
            is_new = not path.exists()
            self._night_file = path.open("a", newline="")
            self._night_path = path
            if is_new:
                csv.writer(self._night_file).writerow(["time", "player", "device", "device_name", "bpm"])
        csv.writer(self._night_file).writerow([round(t, 2), name, device_id, self.device_label(device_id), bpm])
        self._night_file.flush()

    def device_label(self, device_id: str) -> str:
        if alias := self.store.aliases.get(device_id):
            return alias
        dev = self.hub.devices.get(device_id)
        if dev is None:
            return device_id
        return f"{KIND_LABELS.get(dev.kind, dev.kind)} · {dev.name}"

    def snapshot(self) -> dict:
        now = time.time()
        sensitivity = float(self.store.settings.get("sensitivity", 1.0))
        owner = {p["device_id"]: p for p in self.store.players if p["device_id"]}

        seats = []
        for p in self.store.seated():
            tracker = self.trackers.get(p["id"])
            state = tracker.state(now, sensitivity) if tracker else {"status": "waiting"}
            if not p["device_id"]:
                state = {"status": "nodevice"}
            seats.append({
                "player_id": p["id"],
                "name": p["name"],
                "pos": p.get("pos"),
                "device_id": p["device_id"],
                "device_label": self.device_label(p["device_id"]) if p["device_id"] else None,
                **state,
            })

        # named devices stay listed while switched off, so straps can be
        # assigned before anyone puts them on
        known = dict(self.hub.devices)
        for device_id in self.store.aliases:
            if device_id not in known:
                source = device_id.split(":", 1)[0]
                known[device_id] = Device(id=device_id, source=source, name="not seen yet")

        devices = []
        for dev in sorted(known.values(), key=lambda d: (d.source, self.device_label(d.id))):
            d = dev.to_dict(now)
            d["label"] = self.device_label(dev.id)
            d["alias"] = self.store.aliases.get(dev.id, "")
            d["kind_label"] = KIND_LABELS.get(dev.kind, dev.kind)
            d["live"] = bool(dev.last_sample) and now - dev.last_sample < 6
            d["assigned_to"] = owner[dev.id]["name"] if dev.id in owner else None
            devices.append(d)

        self._update_events(now, seats)
        return {
            "now": now,
            "events": list(self.events),
            "game": {"started_at": self.started_at, "elapsed": round(now - self.started_at)},
            "settings": self.store.settings,
            "seats": seats,
            "players": self.store.players,
            "devices": devices,
        }

    def _emit(self, now: float, **event) -> None:
        self._event_id += 1
        self.events.append({"id": self._event_id, "t": now, **event})

    def _update_events(self, now: float, seats: list[dict]) -> None:
        """A tilt alert when someone tips over, at most once a minute each."""
        for s in seats:
            pid = s["player_id"]
            active = s["status"] == "live" and not s["calibrating"]
            level = s["level"] if active else 0
            prev = self._prev_level.get(pid, 0)
            self._prev_level[pid] = level
            if level == 2 and prev < 2 and now - self._last_tilt_alert.get(pid, -1e9) >= TILT_ALERT_COOLDOWN:
                self._last_tilt_alert[pid] = now
                self._emit(now, type="tilt", name=s["name"], bpm=s["bpm"], delta=round(s["delta"]))
