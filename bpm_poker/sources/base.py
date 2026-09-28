"""The contract every heart-rate integration implements.

To add a new kind of device (ANT+ stick, a cloud API, a phone relay, ...):

1. Create a module in this package with a subclass of HeartRateSource.
2. Give it a unique `key` and decorate it with @register.
3. In run(), call hub.device(...) for each device you find and
   hub.publish(device_id, bpm) for each reading.
4. Import the module in sources/__init__.py.

Nothing else in the app needs to change: the setup screen lists the new
devices, players can be assigned to them, and the display picks them up.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from ..hub import Hub


class HeartRateSource(ABC):
    key: ClassVar[str]

    @abstractmethod
    async def run(self, hub: Hub) -> None:
        """Discover devices and publish readings until cancelled."""


SOURCES: dict[str, type[HeartRateSource]] = {}


def register(cls: type[HeartRateSource]) -> type[HeartRateSource]:
    SOURCES[cls.key] = cls
    return cls
