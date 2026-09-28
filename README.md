# BPM Poker

Live heart rates for everyone at the poker table, on one shared screen. Each
player's tile turns from cyan to yellow to pink as their heart rate climbs
above their own average for the night, so everyone can see who's sweating a
hand.

Works with anything that broadcasts standard Bluetooth heart rate: chest straps
(Coospo, Polar, Garmin…), Whoop with Broadcast Heart Rate on, and Fitbit/Pixel
devices with heart-rate sharing. Runs on a Mac.

```
uv run bpm-poker            # real devices over Bluetooth
uv run bpm-poker --sim 7    # 7 simulated players, for trying it out
```

This opens **Setup** (`localhost:8000/setup`) on the laptop. Put the
**Display** (`localhost:8000/`) on the second monitor and click it to go full
screen.

## Game night

### Once, before the first game (~15 min)

1. **Number your chest straps.** Put each on, and when it appears under
   *Devices* in Setup, type its number into the name box ("Strap 3"). Setup
   remembers it. On straps with a clip-on pod, the pod is what gets recognised,
   so number the pods.
2. **Whoop:** Whoop app → More → Device Settings → turn on **Broadcast Heart
   Rate**. If the toggle doesn't respond, delete and reinstall the Whoop app,
   then reconnect the band.
3. **Fitbit Air (and other Fitbit/Pixel devices):** Google Health app →
   Connections → the device → **Share heart rate** → Get started. It can only
   share with one app at a time, so close Strava, Peloton and similar apps.
4. **Add your regulars** under *At the table* and pick each one's device.
   People with their own wearable keep it from game to game.

Named devices stay listed even when switched off, so you can hand out straps
and assign them before anyone puts one on.

### Each game (~2 min)

1. Plug in the laptop and the monitor, and run `uv run bpm-poker`.
2. Put the **Display** on the monitor and click it to go full screen.
3. Hand out straps. Wet the two electrode pads so they read straight away.
4. In Setup:
   - **Deal in** who's playing and **Sit out** who isn't.
   - Pick each strap wearer's strap from their dropdown.
   - Pick the layout (below) and arrange the seats.
5. Click **New game** when cards are dealt. For the first 90 seconds each tile
   shows *learning* while it builds that player's baseline.

### Layouts

- **Flat in the middle:** the monitor lies on the table. Tiles sit around the
  edge, each facing the person on that side. Drag a tile along the edge to line
  it up with its player. Seat 1 is at the bottom, then clockwise.
- **End of table:** the monitor stands at one end and everyone looks at it. The
  screen is a map of the table from above, with the monitor's end at the top:
  a column for each long side and one seat at the far end. Seat 1 is the far
  end, then clockwise.
- **Grid:** everyone upright in rows.

In any layout, dropping one tile on another swaps them. If the monitor is on
its side, **Turn 90°** rotates the picture to match, and **Flip** turns it
upside down, with no need to change macOS display settings.

## Reading the screen

Each tile shows a name, the current heart rate, and how far that is from the
player's own average for the game (`+14`, `−3`). No resting heart rate is
needed.

| Colour | Level | Meaning |
|---|---|---|
| Cyan | Cool | around their normal |
| Yellow | Warm | clearly up |
| Pink | Tilt | way up; the tile shakes, and more static appears the higher they go |

When someone tips into Tilt, a big **tilt alert** with their name plays for 5
seconds, at most once a minute per player. With the screen flat in the middle
it shows twice, back to back, so both sides can read it. At the end of the
table it shows once, in a band kept clear at the top.

**Chill / Normal / Twitchy** in Setup changes how easily people get flagged.

### How spikes are measured

- Each player's **baseline** is a running average with about 10 minutes of
  memory, so it follows slow drift (a drink, a warm room) but not a single hand.
  It starts from the median of the first 90 seconds, so a strap's first few
  junk readings can't skew it.
- The baseline updates **5× slower while someone is well above it**, so a long,
  tense hand doesn't become their new normal.
- The **spike score** is how far the lightly smoothed current rate sits above
  the baseline, divided by how much that player normally varies (5–10 bpm).
  Warm starts at 1, Tilt at 2.
  - Someone who sits at 95 all night isn't flagged.
  - Someone who goes from 68 to 84 on the river is.
- Readings under 45 bpm are ignored: that's a strap finding a pulse, not a heart.

All the numbers are at the top of `bpm_poker/tracker.py`.

## After the game

Every reading is saved on the laptop in `data/`, which is git-ignored and never
leaves your machine:

- `data/games/<start>.csv`: one file per game (a new one each time you click
  New game).
- `data/nights/<date>.csv`: every reading from every device for the whole
  night, across restarts.

Charts and shareable cards:

```
uv run python -m bpm_poker.review --minutes 10                   # last 10 minutes, a panel per player
uv run python -m bpm_poker.review                                # the whole night
uv run python -m bpm_poker.infographics                          # five cards for the group chat
uv run python -m bpm_poker.infographics --challenge 18:39-18:47  # ...plus a card for a max-heart-rate challenge
```

The infographics are 1080×1350 PNGs: the night in numbers, awards, who was on
tilt and when, and how each person's night felt. They're written to
`data/infographics/<date>/`.

## Troubleshooting

| Problem | Fix |
|---|---|
| A strap doesn't show up | Wet the pads and make sure it's tight. Close the Coospo, Strava, etc. apps on nearby phones, since a strap talks to one app at a time. If it still doesn't appear, the battery may be flat. |
| A Whoop doesn't show up | Check Broadcast Heart Rate is still on (updates can switch it off). If the toggle won't respond, reinstall the Whoop app. |
| A Fitbit drops out | Heart-rate sharing stops when its connection drops. Tap Share heart rate → Get started again. |
| "No signal" on a tile | The strap lost skin contact or went out of range. It reconnects on its own. |
| Tiles stuck after the Mac slept | They recover on their own within about 20 seconds: a silent connection is dropped and redone, and scanning restarts after sleep. Keep the laptop plugged in. |
| The Mac asks for Bluetooth permission | Allow it for the terminal app: System Settings → Privacy & Security → Bluetooth. |

## Adding a new kind of device

Everything that reads heart rate is a **source** in `bpm_poker/sources/`:

- `ble_hrs.py`: the standard Bluetooth heart rate service. Covers chest straps,
  Whoop broadcast, Fitbit sharing and most others.
- `simulator.py`: simulated players.
- `push.py`: anything that can send an HTTP request, e.g.
  `curl -XPOST localhost:8000/api/push/daves-watch -H 'content-type: application/json' -d '{"bpm": 80}'`.
  Start with `--lan` to accept these from other devices on the network.

To add one, subclass `HeartRateSource` and give it a `key`. In `run()`, call
`hub.device(...)` for each device it finds and `hub.publish(device_id, bpm)`
for each reading. Setup, players, spike detection and the display then work
with it unchanged.

```
sources ──> Hub (devices + readings) ──> Game (player ↔ device, SpikeTracker per player) ──> WebSocket ──> Display / Setup
```

Tests: `uv run pytest`
