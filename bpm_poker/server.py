"""Web server: the display (/), the setup screen (/setup), a small REST API
for setup actions, and a WebSocket that streams the live snapshot."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .game import Game
from .hub import Hub
from .sources import HeartRateSource, PushSource
from .store import Store

log = logging.getLogger(__name__)
WEB = Path(__file__).parent / "web"
TICK = 0.25  # s between snapshots pushed to screens


class NewPlayer(BaseModel):
    name: str


class PlayerPatch(BaseModel):
    name: str | None = None
    device_id: str | None = None
    clear_device: bool = False
    seated: bool | None = None


class SeatOrder(BaseModel):
    order: list[str]


class Layout(BaseModel):
    positions: dict[str, float]


class DevicePatch(BaseModel):
    alias: str


class SettingsPatch(BaseModel):
    layout: str | None = None
    rotation: float | None = None
    turn: float | None = None
    sensitivity: float | None = None


class PushReading(BaseModel):
    bpm: int


def create_app(sources: list[HeartRateSource], data_dir: Path) -> FastAPI:
    hub = Hub()
    store = Store(data_dir / "state.json")
    game = Game(hub, store, data_dir / "games")
    push = PushSource(hub)
    clients: set[WebSocket] = set()

    async def broadcast_loop() -> None:
        while True:
            await asyncio.sleep(TICK)
            if not clients:
                continue
            msg = json.dumps(game.snapshot())
            for ws in list(clients):
                try:
                    await ws.send_text(msg)
                except Exception:
                    clients.discard(ws)

    async def run_source(src: HeartRateSource) -> None:
        try:
            await src.run(hub)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("source %s crashed", src.key)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        tasks = [asyncio.create_task(run_source(s)) for s in sources]
        tasks.append(asyncio.create_task(broadcast_loop()))
        yield
        for t in tasks:
            t.cancel()

    app = FastAPI(lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/")
    def display():
        return FileResponse(WEB / "display.html")

    @app.get("/setup")
    def setup():
        return FileResponse(WEB / "setup.html")

    @app.get("/api/state")
    def state():
        return game.snapshot()

    @app.post("/api/players")
    def add_player(body: NewPlayer):
        if not body.name.strip():
            raise HTTPException(400, "name required")
        return store.add_player(body.name)

    @app.patch("/api/players/{player_id}")
    def update_player(player_id: str, body: PlayerPatch):
        try:
            before = store.player(player_id)["device_id"]
        except KeyError:
            raise HTTPException(404)
        fields = body.model_dump(exclude_none=True, exclude={"clear_device"})
        if body.clear_device:
            fields["device_id"] = None
        p = store.update_player(player_id, **fields)
        if p["device_id"] != before or body.seated is False:
            game.reset_player(player_id)
        return p

    @app.delete("/api/players/{player_id}")
    def remove_player(player_id: str):
        store.remove_player(player_id)
        game.reset_player(player_id)
        return {"ok": True}

    @app.post("/api/seats")
    def seat_order(body: SeatOrder):
        store.set_seat_order(body.order)
        return {"ok": True}

    @app.post("/api/layout")
    def layout(body: Layout):
        store.set_positions(body.positions)
        return {"ok": True}

    @app.patch("/api/devices/{device_id:path}")
    def rename_device(device_id: str, body: DevicePatch):
        store.set_alias(device_id, body.alias)
        return {"ok": True}

    @app.patch("/api/settings")
    def update_settings(body: SettingsPatch):
        store.update_settings(**body.model_dump())
        return store.settings

    @app.post("/api/game/new")
    def new_game():
        game.new_game()
        return {"ok": True}

    @app.post("/api/push/{name}")
    def push_reading(name: str, body: PushReading):
        return {"device_id": push.ingest(name, body.bpm)}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        clients.add(websocket)
        await websocket.send_text(json.dumps(game.snapshot()))
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            clients.discard(websocket)

    return app
