"""Start BPM Poker.

    uv run bpm-poker              # real devices over Bluetooth
    uv run bpm-poker --sim 6      # add 6 simulated players (testing)
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

from .server import create_app
from .sources import BleHeartRateSource, SimulatedSource


def main() -> None:
    ap = argparse.ArgumentParser(prog="bpm-poker")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--lan", action="store_true", help="listen on the network so phones/tablets can open the display")
    ap.add_argument("--sim", type=int, default=0, metavar="N", help="add N simulated heart-rate devices")
    ap.add_argument("--no-ble", action="store_true", help="don't use Bluetooth")
    ap.add_argument("--no-open", action="store_true", help="don't open the setup page in a browser")
    ap.add_argument("--data", type=Path, default=Path.cwd() / "data")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")

    sources = []
    if not args.no_ble:
        if BleHeartRateSource is None:
            sys.exit("bleak is not installed; run with --no-ble or `uv sync`")
        sources.append(BleHeartRateSource())
    if args.sim:
        sources.append(SimulatedSource(args.sim))

    if sys.platform == "darwin":
        # keep the Mac and the displays awake for the whole game
        subprocess.Popen(["caffeinate", "-dims", "-w", str(os.getpid())])

    url = f"http://localhost:{args.port}"
    print(f"\n  Setup:   {url}/setup\n  Display: {url}/\n")
    if not args.no_open:
        threading.Timer(1.5, webbrowser.open, [f"{url}/setup"]).start()

    app = create_app(sources, args.data)
    uvicorn.run(app, host="0.0.0.0" if args.lan else "127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
