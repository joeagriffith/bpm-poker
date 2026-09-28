"""HTTP push: anything that can send a web request can be a heart-rate device.

    curl -X POST localhost:8000/api/push/dave-watch -d '{"bpm": 81}' \
         -H 'content-type: application/json'

A device appears as "push:<key>" on first reading. It's a fallback for
wearables that can't broadcast over Bluetooth but can run a phone shortcut
or app that posts readings.
"""

from __future__ import annotations

from ..hub import Hub
from .base import HeartRateSource, register


@register
class PushSource(HeartRateSource):
    key = "push"

    def __init__(self, hub: Hub) -> None:
        self.hub = hub

    async def run(self, hub: Hub) -> None:
        pass  # passive: readings arrive via ingest(), called from the web server

    def ingest(self, name: str, bpm: int) -> str:
        device_id = f"push:{name}"
        self.hub.device(device_id, self.key, name=name, kind="push", connected=True)
        self.hub.publish(device_id, bpm)
        return device_id
