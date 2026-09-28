"""Fake heart-rate devices for testing the display without anyone wearing a strap.

Each simulated person idles around a resting rate and occasionally has a
spike: a big hand, a bluff getting called.
"""

from __future__ import annotations

import asyncio
import random

from ..hub import Hub
from .base import HeartRateSource, register


@register
class SimulatedSource(HeartRateSource):
    key = "sim"

    def __init__(self, count: int = 6) -> None:
        self.count = count

    async def run(self, hub: Hub) -> None:
        await asyncio.gather(*(self._person(hub, i) for i in range(self.count)))

    async def _person(self, hub: Hub, i: int) -> None:
        device_id = f"sim:{i + 1}"
        hub.device(device_id, self.key, name=f"Simulated {i + 1}", kind="sim", connected=True, battery=100)
        rest = random.uniform(62, 88)
        bpm = rest
        spike = 0.0  # current spike height in bpm
        spike_target = 0.0
        while True:
            if spike_target == 0 and random.random() < 1 / 60:
                spike_target = random.uniform(8, 35)
            elif spike_target and random.random() < 1 / 25:
                spike_target = 0.0
            spike += (spike_target - spike) * 0.15
            bpm += (rest + spike - bpm) * 0.3 + random.gauss(0, 1.2)
            hub.publish(device_id, round(bpm))
            await asyncio.sleep(1)
