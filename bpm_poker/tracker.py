"""Per-player spike detection against their own in-game baseline.

No resting heart rate needed. Each player's baseline is a slow running
average built during the game, and a spike is how far their (lightly
smoothed) current rate sits above it, scaled by how much they normally
wander. Someone who sits at 95 all night isn't flagged; someone who jumps
from 68 to 84 on the river is.
"""

from __future__ import annotations

from collections import deque
from math import exp
from statistics import median

CURRENT_TAU = 5.0  # s: smoothing on the live signal, so one noisy beat doesn't flash red
BASELINE_TAU = 600.0  # s: the running average; ~10 min memory so it follows slow drift
SPIKE_SLOWDOWN = 5.0  # baseline adapts this much slower while spiking, so a long tense hand doesn't become the new normal
WARMUP = 90.0  # s: median of readings while a baseline forms; nobody is flagged during this
MIN_BPM, MAX_BPM = 45, 230  # outside this it's a strap finding (or losing) a pulse, not a heart
DEV_TAU = 300.0  # s: memory for how much this player normally wanders
MIN_SCALE, MAX_SCALE = 5.0, 10.0  # bpm above baseline that counts as one "step" of heat
HISTORY_SECONDS = 300
TREND_WINDOW = 10  # s
TREND_BPM = 4.0  # change over TREND_WINDOW that counts as rising/falling
STALE_AFTER = 6.0  # s without a reading before showing "no signal"
WARM_Z, TILT_Z = 1.0, 2.0  # spike score thresholds for the three levels


class SpikeTracker:
    def __init__(self) -> None:
        self.started: float | None = None
        self.last_t: float | None = None
        self.last_bpm: int | None = None
        self.current = 0.0
        self.baseline = 0.0
        self.dev = 3.0
        self.peak_delta = 0.0
        self.peak_bpm = 0
        self._warm: list[int] = []
        self._hist_t = 0.0
        self.history: deque[float] = deque(maxlen=HISTORY_SECONDS)

    @property
    def scale(self) -> float:
        return min(max(1.5 * self.dev, MIN_SCALE), MAX_SCALE)

    def add(self, t: float, bpm: int) -> None:
        if not MIN_BPM <= bpm <= MAX_BPM:
            return
        if self.last_t is None:
            self.started = self.last_t = t
            self.current = self.baseline = float(bpm)
        dt = min(max(t - self.last_t, 0.0), 5.0)
        self.last_t = t
        self.last_bpm = bpm
        self.peak_bpm = max(self.peak_bpm, bpm)
        self.current += (1 - exp(-dt / CURRENT_TAU)) * (bpm - self.current)

        if t - self.started < WARMUP:
            # median, so a strap spending its first minute on junk can't drag the baseline
            self._warm.append(bpm)
            self.baseline = float(median(self._warm))
        else:
            delta = self.current - self.baseline
            tau = BASELINE_TAU * (SPIKE_SLOWDOWN if delta > self.scale else 1)
            self.baseline += (1 - exp(-dt / tau)) * delta
            self.peak_delta = max(self.peak_delta, delta)
        self.dev += (1 - exp(-dt / DEV_TAU)) * (min(abs(self.current - self.baseline), 2 * MAX_SCALE) - self.dev)

        if t - self._hist_t >= 1:
            self.history.append(round(self.current, 1))
            self._hist_t = t

    def state(self, now: float, sensitivity: float = 1.0) -> dict:
        if self.last_t is None:
            return {"status": "waiting"}
        calibrating = now - self.started < WARMUP
        delta = self.current - self.baseline
        z = 0.0 if calibrating else max(0.0, delta / self.scale * sensitivity)
        h = self.history
        trend = 0
        if len(h) > TREND_WINDOW:
            change = h[-1] - h[-1 - TREND_WINDOW]
            trend = 1 if change >= TREND_BPM else -1 if change <= -TREND_BPM else 0
        return {
            "status": "stale" if now - self.last_t > STALE_AFTER else "live",
            "bpm": self.last_bpm,
            "smooth": round(self.current, 1),
            "baseline": round(self.baseline, 1),
            "delta": round(delta, 1),
            "z": round(z, 2),
            "level": 0 if z < WARM_Z else 1 if z < TILT_Z else 2,  # cool / warm / tilt
            "intensity": round(min(z / 3, 1.0), 3),
            "trend": trend,
            "calibrating": calibrating,
            "calibration_left": max(0, round(WARMUP - (now - self.started))),
            "peak_delta": round(self.peak_delta, 1),
            "peak_bpm": self.peak_bpm,
        }
