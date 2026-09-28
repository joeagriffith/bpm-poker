from bpm_poker.sources.ble_hrs import classify, parse_heart_rate
from bpm_poker.store import Store
from bpm_poker.tracker import WARMUP, SpikeTracker


def test_parse_uint8_with_contact():
    # flags: contact supported (0x04) + detected (0x02), uint8 HR
    assert parse_heart_rate(bytes([0x06, 72])) == (72, True)


def test_parse_uint16_no_contact_with_rr():
    data = bytes([0x01 | 0x04 | 0x10, 0x2C, 0x01, 0x00, 0x04])
    assert parse_heart_rate(data) == (300, False)


def test_parse_contact_not_supported():
    assert parse_heart_rate(bytes([0x00, 64])) == (64, None)


def test_classify():
    assert classify("WHOOP 4C0123456") == "whoop"
    assert classify("WBB5AP2062792") == "whoop"
    assert classify("H808S 12345") == "coospo"
    assert classify("Polar H10 1234") == "generic"


def feed(tracker, start, seconds, bpm_fn):
    for s in range(seconds):
        tracker.add(start + s, bpm_fn(s))
    return start + seconds


def test_calibrating_then_steady():
    tr = SpikeTracker()
    t = feed(tr, 0, 60, lambda s: 70 + (s % 3) - 1)
    st = tr.state(t)
    assert st["calibrating"] and st["level"] == 0
    t = feed(tr, t, 300, lambda s: 70 + (s % 3) - 1)
    st = tr.state(t)
    assert not st["calibrating"]
    assert abs(st["baseline"] - 70) < 1.5
    assert st["level"] == 0


def test_spike_is_flagged_and_baseline_holds():
    tr = SpikeTracker()
    t = feed(tr, 0, 600, lambda s: 70)
    t = feed(tr, t, 60, lambda s: 90)  # +20 for a minute: a big hand
    st = tr.state(t)
    assert st["level"] >= 2
    assert st["baseline"] < 72  # the spike didn't drag the average up much
    t = feed(tr, t, 60, lambda s: 70)
    assert tr.state(t)["level"] == 0


def test_high_resting_player_not_flagged():
    tr = SpikeTracker()
    t = feed(tr, 0, 900, lambda s: 98 + (s % 5) - 2)
    assert tr.state(t)["level"] == 0


def test_sensitivity_scales():
    tr = SpikeTracker()
    t = feed(tr, 0, 600, lambda s: 70)
    t = feed(tr, t, 40, lambda s: 78)
    assert tr.state(t, 0.7)["level"] < tr.state(t, 1.4)["level"]


def test_stale():
    tr = SpikeTracker()
    feed(tr, 0, 10, lambda s: 70)
    assert tr.state(30)["status"] == "stale"


def test_store_device_unique_and_seat_order(tmp_path):
    st = Store(tmp_path / "s.json")
    a, b, c = (st.add_player(n) for n in "ABC")
    st.update_player(a["id"], device_id="ble:1")
    st.update_player(b["id"], device_id="ble:1")
    assert st.player(a["id"])["device_id"] is None
    st.set_seat_order([c["id"], a["id"], b["id"]])
    assert [p["name"] for p in Store(tmp_path / "s.json").seated()] == ["C", "A", "B"]


def test_positions_drive_seat_order_and_reorder_clears_them(tmp_path):
    st = Store(tmp_path / "s.json")
    a, b, c = (st.add_player(n) for n in "ABC")
    st.set_positions({a["id"]: 0.7, b["id"]: 0.1, c["id"]: 0.4})
    assert [p["name"] for p in st.seated()] == ["B", "C", "A"]
    assert st.player(a["id"])["pos"] == 0.7
    st.set_seat_order([a["id"], b["id"], c["id"]])
    assert all("pos" not in p for p in st.players)


def _seat(pid, level, bpm, delta, status="live"):
    return {"player_id": pid, "name": pid.upper(), "status": status, "calibrating": False,
            "level": level, "bpm": bpm, "delta": delta}


def test_tilt_alert_when_tipping_over(tmp_path):
    from bpm_poker.game import Game
    from bpm_poker.hub import Hub

    g = Game(Hub(), Store(tmp_path / "s.json"), tmp_path / "games")
    g._update_events(0, [_seat("a", 0, 70, 0), _seat("b", 0, 80, 0)])
    g._update_events(1, [_seat("a", 2, 95, 22), _seat("b", 1, 88, 8)])
    g._update_events(2, [_seat("a", 2, 101, 27), _seat("b", 2, 96, 15)])
    g._update_events(30, [_seat("a", 0, 75, 3), _seat("b", 0, 80, 0)])
    assert [(e["type"], e["name"], e["delta"]) for e in g.events] == [("tilt", "A", 22), ("tilt", "B", 15)]


def test_tilt_alert_cooldown(tmp_path):
    from bpm_poker.game import Game
    from bpm_poker.hub import Hub

    g = Game(Hub(), Store(tmp_path / "s.json"), tmp_path / "games")
    for t, lvl in enumerate([2, 0, 2, 0, 2]):
        g._update_events(t, [_seat("a", lvl, 90, 20 if lvl else 0)])
    assert sum(e["type"] == "tilt" for e in g.events) == 1


def test_named_devices_listed_when_not_seen(tmp_path):
    from bpm_poker.game import Game
    from bpm_poker.hub import Hub

    st = Store(tmp_path / "s.json")
    st.set_alias("ble:ABC", "Coospo 1")
    devices = Game(Hub(), st, tmp_path / "games").snapshot()["devices"]
    assert [(d["label"], d["live"], d["connected"]) for d in devices] == [("Coospo 1", False, False)]


def test_night_log_keeps_every_device(tmp_path):
    import csv as _csv

    from bpm_poker.game import Game
    from bpm_poker.hub import Hub

    hub, st = Hub(), Store(tmp_path / "s.json")
    p = st.add_player("Ana")
    st.update_player(p["id"], device_id="ble:1")
    Game(hub, st, tmp_path / "data" / "games")
    hub.device("ble:1", "ble", name="Strap")
    hub.device("ble:2", "ble", name="Spare")
    hub.publish("ble:1", 70, t=1_790_000_000)
    hub.publish("ble:2", 80, t=1_790_000_001)
    [night] = (tmp_path / "data" / "nights").glob("*.csv")
    rows = list(_csv.DictReader(night.open()))
    assert [(r["player"], r["bpm"]) for r in rows] == [("Ana", "70"), ("", "80")]


def test_junk_at_start_does_not_skew_baseline():
    tr = SpikeTracker()
    t = feed(tr, 0, 30, lambda s: 40)  # strap still finding a pulse
    t = feed(tr, t, 600, lambda s: 95)
    st = tr.state(t)
    assert abs(st["baseline"] - 95) < 2 and st["level"] == 0
