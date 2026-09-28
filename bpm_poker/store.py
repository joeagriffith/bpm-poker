"""Persistent roster and settings (data/state.json).

Players are remembered between games, along with the device they last used,
so a regular with their own Whoop is set up once and never again.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

DEFAULT_SETTINGS = {
    "layout": "table",  # "table": screen flat mid-table, tiles face each seat; "end": screen at the end of the table; "grid"
    "rotation": 0,  # degrees to spin the ring so seat 1 lines up with the real seat 1
    "turn": 0,  # degrees to turn the whole picture, for a monitor mounted sideways or upside down
    "sensitivity": 1.0,
}


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        data = json.loads(path.read_text()) if path.exists() else {}
        # players: [{id, name, device_id, seated}], list order is seat order
        self.players: list[dict] = data.get("players", [])
        self.aliases: dict[str, str] = data.get("aliases", {})
        self.settings: dict = {**DEFAULT_SETTINGS, **data.get("settings", {})}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {"players": self.players, "aliases": self.aliases, "settings": self.settings}, indent=2
        ))
        os.replace(tmp, self.path)

    def player(self, player_id: str) -> dict:
        for p in self.players:
            if p["id"] == player_id:
                return p
        raise KeyError(player_id)

    def seated(self) -> list[dict]:
        return [p for p in self.players if p["seated"]]

    def add_player(self, name: str) -> dict:
        p = {"id": uuid.uuid4().hex[:8], "name": name.strip(), "device_id": None, "seated": True}
        # new players join the end of the seated block
        idx = max((i + 1 for i, q in enumerate(self.players) if q["seated"]), default=0)
        self.players.insert(idx, p)
        self.save()
        return p

    def update_player(self, player_id: str, **fields) -> dict:
        p = self.player(player_id)
        if fields.get("device_id"):
            # a device belongs to one player at a time
            for other in self.players:
                if other is not p and other["device_id"] == fields["device_id"]:
                    other["device_id"] = None
        p.update(fields)
        self.save()
        return p

    def remove_player(self, player_id: str) -> None:
        self.players = [p for p in self.players if p["id"] != player_id]
        self.save()

    def set_seat_order(self, order: list[str], keep_positions: bool = False) -> None:
        by_id = {p["id"]: p for p in self.players}
        seated = [by_id[i] for i in order if i in by_id]
        rest = [p for p in self.players if p["id"] not in order]
        self.players = seated + rest
        if not keep_positions:
            # reordering from setup goes back to evenly spaced tiles
            for p in self.players:
                p.pop("pos", None)
        self.save()

    def set_positions(self, positions: dict[str, float]) -> None:
        """Where each tile sits around the display edge (0..1, clockwise from
        bottom centre), from dragging on the display. Seat order follows."""
        for p in self.players:
            if p["id"] in positions:
                p["pos"] = positions[p["id"]] % 1.0
        order = sorted((p for p in self.seated() if "pos" in p), key=lambda p: p["pos"])
        self.set_seat_order([p["id"] for p in order], keep_positions=True)

    def set_alias(self, device_id: str, alias: str) -> None:
        if alias.strip():
            self.aliases[device_id] = alias.strip()
        else:
            self.aliases.pop(device_id, None)
        self.save()

    def update_settings(self, **fields) -> None:
        self.settings.update({k: v for k, v in fields.items() if v is not None})
        self.save()
