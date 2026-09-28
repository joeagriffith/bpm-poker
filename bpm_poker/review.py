"""Plot heart rates from saved games.

    uv run python -m bpm_poker.review --minutes 10     # last 10 minutes
    uv run python -m bpm_poker.review                  # the whole night

Reads every CSV in data/games (and data/nights) and writes a PNG with one
small panel per player, all on the same scales, each with its peak marked.
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

GAP = 10.0  # s without a reading: break the line rather than draw across it
MIN_BPM = 45  # below this it's a strap re-finding a pulse, not a heart
INK, INK_2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e0", "#fcfcfb"
LINE, PEAK = "#2a78d6", "#e34948"


def load(data_dir: Path) -> dict[str, list[tuple[float, int]]]:
    """All readings per player name, de-duplicated across overlapping files."""
    seen: set[tuple[str, float]] = set()
    series: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for path in sorted(data_dir.glob("games/*.csv")) + sorted(data_dir.glob("nights/*.csv")):
        with path.open() as f:
            for row in csv.DictReader(f):
                name = row.get("player") or ""
                if not name:
                    continue
                t = float(row["time"])
                if int(row["bpm"]) < MIN_BPM or (name, t) in seen:
                    continue
                seen.add((name, t))
                series[name].append((t, int(row["bpm"])))
    for pts in series.values():
        pts.sort()
    return series


def plot(series, start: float, end: float, out: Path, title: str) -> None:
    window = {n: [(t, b) for t, b in pts if start <= t <= end] for n, pts in series.items()}
    window = {n: pts for n, pts in window.items() if len(pts) > 1}
    if not window:
        sys.exit("no readings in that time window")
    # busiest heart first
    names = sorted(window, key=lambda n: -max(b for _, b in window[n]))
    lo = min(b for pts in window.values() for _, b in pts)
    hi = max(b for pts in window.values() for _, b in pts)

    cols = 2 if len(names) > 3 else 1
    rows = -(-len(names) // cols)
    fig, axes = plt.subplots(rows, cols, figsize=(7 * cols, 2.1 * rows + 0.9), sharex=True, sharey=True,
                             squeeze=False, facecolor=SURFACE)
    fig.suptitle(title, x=0.01, ha="left", fontsize=15, fontweight="bold", color=INK)
    for ax in axes.flat:
        ax.set_visible(False)
    for ax, name in zip(axes.flat, names):
        ax.set_visible(True)
        pts = window[name]
        ts = [datetime.fromtimestamp(t) for t, _ in pts]
        bs = [b for _, b in pts]
        ax.set_facecolor(SURFACE)
        seg = [0] + [i for i in range(1, len(pts)) if pts[i][0] - pts[i - 1][0] > GAP] + [len(pts)]
        for a, b in zip(seg, seg[1:]):
            ax.plot(ts[a:b], bs[a:b], color=LINE, linewidth=2, solid_joinstyle="round")
        k = max(range(len(bs)), key=bs.__getitem__)
        ax.plot(ts[k], bs[k], "o", color=PEAK, markersize=8, markeredgecolor=SURFACE, markeredgewidth=2, zorder=3)
        ax.annotate(f"peak {bs[k]}", (ts[k], bs[k]), xytext=(6, 6), textcoords="offset points",
                    fontsize=10, fontweight="bold", color=INK)
        ax.set_title(f"{name}   low {min(bs)} · avg {round(sum(bs) / len(bs))} · peak {bs[k]}",
                     loc="left", fontsize=11, color=INK, fontweight="bold")
        ax.set_ylim(lo - 5, hi + 10)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.tick_params(colors=INK_2, labelsize=9, length=0)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    for ax in axes[:, 0]:
        if ax.get_visible():
            ax.set_ylabel("bpm", color=INK_2, fontsize=9)
    fig.tight_layout()
    fig.savefig(out, dpi=150, facecolor=SURFACE)


def main() -> None:
    ap = argparse.ArgumentParser(prog="bpm_poker.review")
    ap.add_argument("--minutes", type=float, help="only the last N minutes (default: everything)")
    ap.add_argument("--data", type=Path, default=Path.cwd() / "data")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    series = load(args.data)
    if not series:
        sys.exit(f"no saved readings in {args.data}")
    end = max(pts[-1][0] for pts in series.values())
    start = end - args.minutes * 60 if args.minutes else min(pts[0][0] for pts in series.values())
    span = f"last {args.minutes:g} minutes" if args.minutes else "whole night"
    title = f"BPM Poker · {span} · {datetime.fromtimestamp(start):%H:%M}–{datetime.fromtimestamp(end):%H:%M}"
    out = args.out or args.data / "plots" / f"{datetime.fromtimestamp(end):%Y-%m-%d_%H%M}_{span.replace(' ', '-')}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    plot(series, start, end, out, title)
    print(out)
    if not args.no_open and sys.platform == "darwin":
        subprocess.run(["open", str(out)])


if __name__ == "__main__":
    main()
