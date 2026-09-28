"""Shareable infographics of a night's heart rates.

    uv run python -m bpm_poker.infographics
    uv run python -m bpm_poker.infographics --challenge 18:41-18:46

Writes five portrait PNG cards (1080x1350, good for a group chat) to
data/infographics/:

  1. the night in numbers    2. awards    3. the peak challenge (if given)
  4. who was on tilt, and when    5. how each person's night felt

Readings are cleaned first: anything under 45 bpm is dropped, and so is the
first few minutes after a strap drops out, while it re-finds a pulse. Levels
(cool / warm / tilt) are worked out by replaying the same SpikeTracker the
display used.
"""

from __future__ import annotations

import argparse
import math
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import median

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

from .review import load
from .tracker import SpikeTracker

# the display's own colours, so the cards match what everyone saw
BG, PANEL = "#07100d", "#0f1a16"
INK, INK_2, INK_3 = "#f3f6f4", "#a9b5b0", "#66746e"
LEVEL_COLORS = ["#22d3ee", "#facc15", "#ff3d7f"]
LEVEL_NAMES = ["Cool", "Warm", "Tilt"]

RELOCK_AFTER_GAP = 30.0  # s: a gap this long means the strap dropped out...
RELOCK_IGNORE = 180.0  # s: ...and readings for this long afterwards are the strap re-finding a pulse
MAX_STEP = 5.0  # s: longest gap between readings still counted as continuous
BIN = 60.0  # s: time resolution of the tilt timeline
W_IN, H_IN, DPI = 7.2, 9.0, 150  # 1080 x 1350 px

plt.rcParams.update({
    "font.family": ["Avenir Next", "Helvetica Neue", "DejaVu Sans"],
    "text.color": INK,
    "axes.labelcolor": INK_2,
    "xtick.color": INK_3,
    "ytick.color": INK_3,
})


@dataclass
class Player:
    name: str
    raw: list[tuple[float, int]]
    clean: list[tuple[float, int]] = field(default_factory=list)
    levels: list[tuple[float, int, float, float]] = field(default_factory=list)  # (t, level, delta, bpm)

    @property
    def start(self) -> float:
        return self.raw[0][0]

    @property
    def end(self) -> float:
        return self.raw[-1][0]


def clean(pts: list[tuple[float, int]]) -> list[tuple[float, int]]:
    out, ignore_until = [], -1.0
    for i, (t, b) in enumerate(pts):
        if i and t - pts[i - 1][0] > RELOCK_AFTER_GAP:
            ignore_until = t + RELOCK_IGNORE
        if t >= ignore_until:
            out.append((t, b))
    return out


def replay(pts: list[tuple[float, int]]) -> list[tuple[float, int, float, float]]:
    tr, out = SpikeTracker(), []
    for t, b in pts:
        tr.add(t, b)
        st = tr.state(t)
        if st["status"] == "live" and not st["calibrating"]:
            out.append((t, st["level"], st["delta"], b))
    return out


def durations(samples):
    """Pair each sample with the time it stands for (capped, so gaps count as nothing)."""
    for i in range(1, len(samples)):
        dt = samples[i][0] - samples[i - 1][0]
        if dt <= MAX_STEP:
            yield samples[i - 1], dt


def in_window(t: float, window: tuple[float, float] | None) -> bool:
    return window is not None and window[0] <= t <= window[1]


# ---------- analysis ----------


def analyse(series, start: float | None, challenge: tuple[float, float] | None) -> dict:
    if start is None:
        start = auto_start(series)
    players = []
    for name, pts in series.items():
        pts = [(t, b) for t, b in pts if t >= start]
        if len(pts) < 60:
            continue
        p = Player(name, pts)
        p.clean = clean(pts)
        p.levels = replay(p.clean)
        players.append(p)
    if not players:
        sys.exit("not enough readings to make infographics")

    stats = {}
    for p in players:
        share = [0.0, 0.0, 0.0]
        spike = (0.0, 0.0)
        for (t, level, delta, _), dt in durations(p.levels):
            if in_window(t, challenge):
                continue
            share[level] += dt
            if delta > spike[0]:
                spike = (delta, t)
        total = sum(share) or 1
        beats = sum(b / 60 * dt for (t, b), dt in durations(p.raw))
        poker = [(t, b) for t, b in p.clean if not in_window(t, challenge)] or p.clean
        peak_t, peak = max(poker, key=lambda x: x[1])
        stats[p.name] = {
            "share": [s / total for s in share],
            "tilt_min": share[2] / 60,
            "spike": spike,
            "beats": beats,
            "avg": sum(b for _, b in p.clean) / len(p.clean),
            "peak": (peak, peak_t),
        }

    return {
        "players": players,
        "stats": stats,
        "start": min(p.start for p in players),
        "end": max(p.end for p in players),
        "challenge": challenge,
        "hottest": hottest_moment(players, challenge),
        "gaps": table_gaps(players),
    }


def auto_start(series) -> float:
    """First moment at least three people were wearing something."""
    events = sorted((t, n) for n, pts in series.items() for t, _ in pts)
    recent: dict[str, float] = {}
    for t, n in events:
        recent[n] = t
        if sum(1 for x in recent.values() if t - x < 60) >= 3:
            return t
    return events[0][0]


def bins(players) -> dict[str, dict[int, int]]:
    """Typical (median) level for each player in each BIN-second slot."""
    out = {}
    for p in players:
        slots: dict[int, list[int]] = defaultdict(list)
        for t, level, _, _ in p.levels:
            slots[int(t // BIN)].append(level)
        out[p.name] = {k: sorted(v)[len(v) // 2] for k, v in slots.items()}
    return out


def hottest_moment(players, challenge):
    """The slot (outside the challenge) with the most people on tilt at once."""
    count: dict[int, list[str]] = defaultdict(list)
    for name, slots in bins(players).items():
        for k, level in slots.items():
            if level == 2 and not in_window(k * BIN, challenge):
                count[k].append(name)
    if not count:
        return None
    k = max(count, key=lambda k: (len(count[k]), -k))
    return k * BIN, sorted(count[k])


def table_gaps(players, min_len: float = 120.0):
    """Stretches where nobody's device sent anything (e.g. the laptop napping)."""
    times = sorted(t for p in players for t, _ in p.raw)
    return [(a, b) for a, b in zip(times, times[1:]) if b - a >= min_len]


# ---------- drawing helpers ----------


def card(title: str, subtitle: str = ""):
    fig = plt.figure(figsize=(W_IN, H_IN), dpi=DPI, facecolor=BG)
    fig.text(0.07, 0.945, "BPM ♥ POKER", fontsize=11, fontweight="bold", color=LEVEL_COLORS[2])
    fig.text(0.07, 0.895, title, fontsize=27, fontweight="bold", color=INK, va="center")
    if subtitle:
        fig.text(0.07, 0.855, subtitle, fontsize=12.5, color=INK_2, va="center")
    return fig


def footer(fig, text: str) -> None:
    fig.text(0.07, 0.035, text, fontsize=9, color=INK_3, wrap=True)


def fmt_time(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%H:%M")


def fmt_duration(sec: float) -> str:
    h, m = divmod(round(sec / 60), 60)
    return f"{h}h {m:02d}m" if h else f"{m} min"


def legend_row(fig, y: float) -> None:
    x = 0.07
    for color, name in zip(LEVEL_COLORS, LEVEL_NAMES):
        fig.patches.append(FancyBboxPatch((x, y - 0.006), 0.022, 0.014, boxstyle="round,pad=0,rounding_size=0.004",
                                          transform=fig.transFigure, color=color))
        fig.text(x + 0.03, y, name, fontsize=11, color=INK_2, va="center")
        x += 0.16


# ---------- the cards ----------


def card_numbers(a) -> plt.Figure:
    players, stats = a["players"], a["stats"]
    start, end = a["start"], a["end"]
    day = datetime.fromtimestamp(start)
    fig = card("The night in numbers", f"{day:%A %-d %B} · {fmt_time(start)}–{fmt_time(end)}")
    top = max(((p.name, b) for p in players for _, b in p.clean), key=lambda x: x[1])
    tiles = [
        (f"{len(players)}", "players wired up"),
        (fmt_duration(end - start), "of live heart rates"),
        (f"{sum(s['beats'] for s in stats.values()):,.0f}", "heartbeats at the table"),
        (f"{top[1]}", f"highest bpm · {top[0]}"),
        (f"{sum(s['avg'] for s in stats.values()) / len(stats):.0f}", "average bpm around the table"),
        (fmt_duration(sum(s["tilt_min"] for s in stats.values()) * 60), "spent on tilt, all together"),
    ]
    for i, (big, small) in enumerate(tiles):
        col, row = i % 2, i // 2
        x, y = 0.07 + col * 0.44, 0.63 - row * 0.2
        fig.patches.append(FancyBboxPatch((x, y), 0.4, 0.17, boxstyle="round,pad=0,rounding_size=0.02",
                                          transform=fig.transFigure, color=PANEL))
        color = LEVEL_COLORS[[0, 1, 0, 2, 1, 2][i]]
        fig.text(x + 0.035, y + 0.105, big, fontsize=30, fontweight="bold", color=color, va="center")
        fig.text(x + 0.035, y + 0.04, small, fontsize=11.5, color=INK_2, va="center")
    footer(fig, "Live heart rates from Whoop, Fitbit Air and Coospo chest straps, one reading a second.")
    return fig


def card_awards(a) -> plt.Figure:
    stats, challenge = a["stats"], a["challenge"]
    fig = card("Awards", "Poker hours only" + (" · the peak challenge is on its own card" if challenge else ""))

    def best(key, rev=True):
        return sorted(stats, key=key, reverse=rev)[0]

    peak = best(lambda n: stats[n]["peak"][0])
    cool = best(lambda n: stats[n]["share"][0])
    tilt = best(lambda n: stats[n]["tilt_min"])
    spike = best(lambda n: stats[n]["spike"][0])
    avg = best(lambda n: stats[n]["avg"])
    rows = [
        ("Highest heart rate", peak, f"{stats[peak]['peak'][0]} bpm at {fmt_time(stats[peak]['peak'][1])}", 2),
        ("Ice in the veins", cool, f"cool {stats[cool]['share'][0]:.0%} of the night", 0),
        ("Most time on tilt", tilt, f"{stats[tilt]['tilt_min']:.0f} minutes, all told", 2),
        ("Biggest spike", spike, f"+{stats[spike]['spike'][0]:.0f} over their average at {fmt_time(stats[spike]['spike'][1])}", 1),
        ("Hardest-working heart", avg, f"averaged {stats[avg]['avg']:.0f} bpm", 1),
    ]
    if a["hottest"]:
        t, names = a["hottest"]
        rows.append(("Hottest moment", fmt_time(t), f"{len(names)} on tilt at once: {', '.join(names)}", 2))
    step = 0.78 / len(rows)
    for i, (award, who, detail, lvl) in enumerate(rows):
        y = 0.80 - (i + 1) * step
        fig.patches.append(FancyBboxPatch((0.07, y + 0.012), 0.86, step - 0.022, boxstyle="round,pad=0,rounding_size=0.02",
                                          transform=fig.transFigure, color=PANEL))
        fig.patches.append(FancyBboxPatch((0.07, y + 0.012), 0.012, step - 0.022, boxstyle="square,pad=0",
                                          transform=fig.transFigure, color=LEVEL_COLORS[lvl]))
        mid = y + 0.012 + (step - 0.022) / 2
        fig.text(0.115, mid + 0.028, award.upper(), fontsize=10, fontweight="bold", color=LEVEL_COLORS[lvl], va="center")
        fig.text(0.115, mid - 0.004, who, fontsize=22, fontweight="bold", color=INK, va="center")
        fig.text(0.115, mid - 0.036, detail, fontsize=11, color=INK_2, va="center")
    return fig


def card_challenge(a) -> plt.Figure | None:
    if not a["challenge"]:
        return None
    c0, c1 = a["challenge"]
    results = []
    for p in a["players"]:
        during = [(t, b) for t, b in p.raw if c0 <= t <= c1]
        before = [b for t, b in p.clean if c0 - 300 <= t < c0]
        if not during or not before:
            continue
        t_peak, peak = max(during, key=lambda x: x[1])
        clean_times = {t for t, _ in p.clean}
        results.append((p.name, peak, peak - median(before), t_peak not in clean_times))
    results.sort(key=lambda r: -r[1])
    fig = card("The peak challenge", f"{fmt_time(c0)} · everyone tried to max out their heart rate")
    ax = fig.add_axes([0.07, 0.14, 0.86, 0.66], facecolor=BG)
    n = len(results)
    lo = min(r[1] - r[2] for r in results) - 20
    for i, (name, peak, rise, flag) in enumerate(results):
        y = (n - 1 - i) * 1.3
        style = dict(facecolor="none", edgecolor=LEVEL_COLORS[2], hatch="///", linewidth=1.5) if flag \
            else dict(color=LEVEL_COLORS[2])
        ax.barh(y, peak - lo, left=lo, height=0.55, **style)
        ax.text(lo, y + 0.36, f"{i + 1}.  {name}", fontsize=13, fontweight="bold", color=INK, va="bottom")
        ax.text(peak + 2, y + 0.05, f"{peak}{'*' if flag else ''}", fontsize=17, fontweight="bold", color=INK, va="center")
        ax.text(peak + 2, y - 0.3, f"+{rise:.0f}", fontsize=10, color=INK_2, va="center")
    ax.set_ylim(-0.6, (n - 1) * 1.3 + 0.9)
    ax.set_xlim(lo, max(r[1] for r in results) + 30)
    ax.axis("off")
    notes = "Peak bpm during the challenge; +N is how far above their previous five minutes."
    if any(r[3] for r in results):
        notes += "\n* strap was reconnecting at the time, so treat with suspicion."
    footer(fig, notes)
    return fig


def card_timeline(a) -> plt.Figure:
    players, challenge = a["players"], a["challenge"]
    slots = bins(players)
    order = sorted(players, key=lambda p: -a["stats"][p.name]["tilt_min"])
    fig = card("Who was on tilt, and when", "Every minute of the night, coloured by level")
    legend_row(fig, 0.815)
    ax = fig.add_axes([0.2, 0.12, 0.74, 0.66], facecolor=BG)
    start, end = a["start"], a["end"]
    for row, p in enumerate(order):
        y = len(order) - 1 - row
        for k, level in slots[p.name].items():
            t = k * BIN
            ax.add_patch(plt.Rectangle((mdates.date2num(datetime.fromtimestamp(t)), y - 0.36),
                                       BIN / 86400, 0.72, color=LEVEL_COLORS[level], linewidth=0))
        ax.text(-0.02, y, p.name, transform=ax.get_yaxis_transform(), ha="right", va="center",
                fontsize=12.5, fontweight="bold", color=INK)
    x0, x1 = (mdates.date2num(datetime.fromtimestamp(t)) for t in (start, end))
    if challenge:
        c0, c1 = (mdates.date2num(datetime.fromtimestamp(t)) for t in challenge)
        ax.axvspan(c0, c1, color=INK, alpha=0.08, linewidth=0)
        ax.text((c0 + c1) / 2, len(order) - 0.35, "peak\nchallenge", ha="center", va="bottom", fontsize=9, color=INK_2)
    for g0, g1 in a["gaps"]:
        m = mdates.date2num(datetime.fromtimestamp((g0 + g1) / 2))
        ax.text(m, len(order) - 0.35, "no\nsignal", ha="center", va="bottom", fontsize=9, color=INK_3)
    ax.set_xlim(x0, x1)
    ax.set_ylim(-0.6, len(order) + 0.5)
    ax.xaxis.set_major_locator(mdates.HourLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.tick_params(axis="x", labelsize=10, length=0, pad=8)
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    footer(fig, "Tilt means well above their own running average for the night, not a set number.")
    return fig


def card_mix(a) -> plt.Figure:
    stats = a["stats"]
    order = sorted(stats, key=lambda n: stats[n]["share"][0])
    fig = card("How everyone's night felt", "Share of poker time spent cool, warm and on tilt")
    legend_row(fig, 0.815)
    ax = fig.add_axes([0.07, 0.1, 0.86, 0.68], facecolor=BG)
    n = len(order)
    for i, name in enumerate(order):
        y = n - 1 - i
        left = 0.0
        for level in (2, 1, 0):
            w = stats[name]["share"][level]
            if w <= 0:
                continue
            ax.barh(y, w - 0.004, left=left, height=0.5, color=LEVEL_COLORS[level])
            if w >= 0.07:
                ax.text(left + w / 2, y, f"{w:.0%}", ha="center", va="center", fontsize=11,
                        fontweight="bold", color=BG)
            left += w
        ax.text(0, y + 0.33, name, fontsize=13, fontweight="bold", color=INK, va="bottom")
        ax.text(1, y + 0.33, f"avg {stats[name]['avg']:.0f} bpm", fontsize=10.5, color=INK_2, va="bottom", ha="right")
    ax.set_xlim(0, 1)
    ax.set_ylim(-0.5, n)
    ax.axis("off")
    footer(fig, "Sorted from most to least time on tilt and warm. The peak challenge is left out.")
    return fig


# ---------- CLI ----------


def parse_clock(text: str, day: datetime) -> float:
    h, m = map(int, text.split(":"))
    return day.replace(hour=h, minute=m, second=0, microsecond=0).timestamp()


def main() -> None:
    ap = argparse.ArgumentParser(prog="bpm_poker.infographics")
    ap.add_argument("--data", type=Path, default=Path.cwd() / "data")
    ap.add_argument("--start", help="HH:MM the game started (default: when three people were first wired up)")
    ap.add_argument("--challenge", help="HH:MM-HH:MM of a peak-heart-rate challenge, to get its own card")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    series = load(args.data)
    if not series:
        sys.exit(f"no saved readings in {args.data}")
    day = datetime.fromtimestamp(auto_start(series))
    start = parse_clock(args.start, day) if args.start else None
    challenge = tuple(parse_clock(x, day) for x in args.challenge.split("-")) if args.challenge else None

    a = analyse(series, start, challenge)
    out = args.out or args.data / "infographics" / f"{day:%Y-%m-%d}"
    out.mkdir(parents=True, exist_ok=True)
    cards = [("1-numbers", card_numbers), ("2-awards", card_awards), ("3-challenge", card_challenge),
             ("4-timeline", card_timeline), ("5-mix", card_mix)]
    for name, make in cards:
        fig = make(a)
        if fig is None:
            continue
        path = out / f"{name}.png"
        fig.savefig(path, dpi=DPI, facecolor=BG)
        plt.close(fig)
        print(path)
    if not args.no_open and sys.platform == "darwin":
        subprocess.run(["open", str(out)])


if __name__ == "__main__":
    main()
