"""The device hub: the one place every heart-rate source reports into.

Sources know nothing about players or games. They register the devices they
find and publish BPM samples keyed by a device id. Everything downstream
(the game, the setup screen, the display) reads from here.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Callable

SampleListener = Callable[[str, int, float], None]


@dataclass
class Device:
    id: str  # globally unique, prefixed with the source key, e.g. "ble:DB5D19AC-..."
    source: str  # which source owns it: "ble", "sim", "push", ...
    name: str = ""  # what the device calls itself
    kind: str = "generic"  # "whoop", "coospo", "fitbit", "generic", "sim", "push"
    connected: bool = False
    bpm: int | None = None
    last_sample: float = 0.0
    last_seen: float = 0.0
    rssi: int | None = None
    battery: int | None = None
    error: str | None = None

    def to_dict(self, now: float) -> dict:
        d = asdict(self)
        d["age"] = round(now - self.last_sample, 1) if self.last_sample else None
        return d


class Hub:
    def __init__(self) -> None:
        self.devices: dict[str, Device] = {}
        self._listeners: list[SampleListener] = []

    def on_sample(self, fn: SampleListener) -> None:
        self._listeners.append(fn)

    def device(self, device_id: str, source: str, **fields) -> Device:
        """Create or update a device. Fields passed as None are left unchanged."""
        dev = self.devices.get(device_id)
        if dev is None:
            dev = self.devices[device_id] = Device(id=device_id, source=source)
        for key, value in fields.items():
            if value is not None:
                setattr(dev, key, value)
        return dev

    def publish(self, device_id: str, bpm: int, t: float | None = None) -> None:
        t = time.time() if t is None else t
        dev = self.devices.get(device_id)
        if dev is None:
            return
        dev.bpm = bpm
        dev.last_sample = t
        dev.last_seen = t
        for fn in self._listeners:
            fn(device_id, bpm, t)
