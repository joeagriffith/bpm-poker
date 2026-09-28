"""Bluetooth LE Heart Rate Service (0x180D).

This is the standard every chest strap speaks, and what wearables use when
they "broadcast" heart rate. One integration covers:

- Coospo H808S (and any other BLE chest strap or armband)
- Whoop, once Heart Rate Broadcast is switched on in the Whoop app
- Fitbit/Pixel devices that offer heart-rate sharing to gym equipment

The source scans continuously and connects to every device advertising the
service. It reconnects on its own when a strap drops out or is taken off and
put back on.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from ..hub import Hub
from .base import HeartRateSource, register

log = logging.getLogger(__name__)

HR_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
HR_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"

RETRY_COOLDOWN = 3.0  # seconds between connection attempts to the same device
STALL_AFTER = 15.0  # s of silence on a "connected" link before we drop and reconnect it
SCAN_STALL_AFTER = 30.0  # s with no advertisements at all before restarting the scanner


def classify(name: str) -> str:
    n = name.lower()
    if "whoop" in n or re.match(r"^wb[a-z0-9]{6,}$", n):
        return "whoop"
    if "coospo" in n or "h808" in n or "h6" in n or "h9z" in n:
        return "coospo"
    if "fitbit" in n or "charge" in n or "pixel" in n or "air" in n:
        return "fitbit"
    return "generic"


def parse_heart_rate(data: bytes | bytearray) -> tuple[int, bool | None]:
    """Decode a Heart Rate Measurement (0x2A37) notification.

    Returns (bpm, skin_contact). skin_contact is None if the sensor doesn't
    report it.
    """
    flags = data[0]
    bpm = int.from_bytes(data[1:3], "little") if flags & 0x01 else data[1]
    contact = bool(flags & 0x02) if flags & 0x04 else None
    return bpm, contact


@register
class BleHeartRateSource(HeartRateSource):
    key = "ble"

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._last_attempt: dict[str, float] = {}
        # CoreBluetooth gets unreliable when several connects run at once.
        self._connect_lock = asyncio.Lock()

    async def run(self, hub: Hub) -> None:
        self.hub = hub
        self._last_adv = time.monotonic()
        scanner = BleakScanner(self._on_advertisement)
        await scanner.start()
        log.info("BLE scanning for heart-rate devices")
        try:
            # After the Mac sleeps, CoreBluetooth can quietly stop scanning.
            # If we hear nothing for a while (or the clock jumped because we
            # were asleep), restart the scanner.
            tick = time.time()
            while True:
                await asyncio.sleep(5)
                slept = time.time() - tick > 20
                tick = time.time()
                if slept or time.monotonic() - self._last_adv > SCAN_STALL_AFTER:
                    log.info("restarting BLE scanner (%s)", "woke from sleep" if slept else "no advertisements")
                    try:
                        await scanner.stop()
                    except Exception:
                        pass
                    scanner = BleakScanner(self._on_advertisement)
                    await scanner.start()
                    self._last_adv = time.monotonic()
        finally:
            await scanner.stop()
            for task in self._tasks.values():
                task.cancel()

    def _on_advertisement(self, device: BLEDevice, adv: AdvertisementData) -> None:
        self._last_adv = time.monotonic()
        if HR_SERVICE not in (u.lower() for u in adv.service_uuids):
            return
        now = time.time()
        device_id = f"ble:{device.address}"
        name = device.name or adv.local_name or "Heart-rate sensor"
        self.hub.device(
            device_id, self.key, name=name, kind=classify(name), rssi=adv.rssi, last_seen=now
        )
        task = self._tasks.get(device_id)
        if task and not task.done():
            return
        if now - self._last_attempt.get(device_id, 0) < RETRY_COOLDOWN:
            return
        self._last_attempt[device_id] = now
        self._tasks[device_id] = asyncio.create_task(self._session(device_id, device))

    async def _session(self, device_id: str, device: BLEDevice) -> None:
        """One connection lifetime. When it ends, the next advertisement reconnects."""
        hub = self.hub
        disconnected = asyncio.Event()
        last_rx = time.monotonic()

        def on_notify(_, data: bytearray) -> None:
            nonlocal last_rx
            last_rx = time.monotonic()
            try:
                bpm, contact = parse_heart_rate(data)
            except IndexError:
                return
            if bpm > 0 and contact is not False:
                hub.publish(device_id, bpm)

        client = BleakClient(device, disconnected_callback=lambda _: disconnected.set(), timeout=15)
        try:
            async with self._connect_lock:
                await client.connect()
                await client.start_notify(HR_MEASUREMENT, on_notify)
            hub.device(device_id, self.key, connected=True, error="")
            log.info("connected %s (%s)", hub.devices[device_id].name, device_id)
            try:
                battery = await client.read_gatt_char(BATTERY_LEVEL)
                hub.device(device_id, self.key, battery=battery[0])
            except Exception:
                pass  # battery service is optional
            last_rx = time.monotonic()
            # A link can claim to be connected while nothing arrives (e.g. after
            # the Mac wakes from sleep). Treat long silence as a disconnect.
            while not disconnected.is_set():
                try:
                    await asyncio.wait_for(disconnected.wait(), timeout=5)
                except asyncio.TimeoutError:
                    if time.monotonic() - last_rx > STALL_AFTER:
                        log.warning("no data from %s for %.0fs, reconnecting", device_id, STALL_AFTER)
                        break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("connection to %s failed: %s", device_id, e)
            hub.device(device_id, self.key, error=str(e) or type(e).__name__)
        finally:
            hub.device(device_id, self.key, connected=False)
            if client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    pass
