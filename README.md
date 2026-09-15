# Better Lighting

A Home Assistant integration that combines adaptive lighting, relative light groups and
scene cycling into one coherent model, so a wall switch can do the obvious thing:

- **First press** turns the room on in **adaptive mode** — brightness and colour temperature
  follow the sun.
- **Each further press** cycles that room's scenes, wrapping back to adaptive.
- **Each switch** can have its own scene order, so the one by the oven starts with *Cooking*
  while the one by the door starts with something else.
- An external automation can put the whole house into **movie night** and have it come back
  correctly afterwards, relighting only the lights that were on beforehand.
- Presence sensors, covers and window sensors modulate all of it.

It replaces the combination of [adaptive-lighting], [relative-light-group] and [scenery]
plus the glue automations needed to make them cooperate — and adds the things none of them
can express: per-light calibration offsets, per-switch cycle orders, and scenes that
override only brightness *or* only colour while the other keeps tracking the sun.

[adaptive-lighting]: https://github.com/basnijholt/adaptive-lighting
[relative-light-group]: https://github.com/Cheerpipe/relative-light-group
[scenery]: https://github.com/j9brown/scenery

> **Status: beta.** Milestones 1 to 7 of 8 are complete — all nine original requirements are
> implemented, and the integration survives restarts and reloads. Packaging (M8) remains.
> Nothing has yet been exercised against a live Home Assistant instance; the test suite uses
> real light entities and the real service path, but a dry run is the sensible next step.

## Concepts

One **hub** entry owns everything else as config subentries:

| Object | What it is |
|---|---|
| **Zone** | One room's lights, controlled together. Exposes a `light` entity. |
| **Scene** | A reusable recipe — brightness, one colour, and which of those it overrides. Not tied to a room. |
| **Controller** | A light switch, with its own ordered list of scenes to cycle through. |
| **Light profile** | Per-light calibration: min/max brightness and colour temperature, plus offsets to match a mismatched bulb to its neighbours. |
| **Mode** | A cross-zone mode such as Home Cinema: named states, and what each zone does in each state. |

### One light, one zone

A light entity may belong to **exactly one zone**, and the config flow enforces it. This is
the constraint the rest of the design rests on: because a light has a single owner, manual
change detection, render ownership and switch-press attribution are all unambiguous, with
none of the multi-owner disambiguation that makes this hard elsewhere.

### Three layers of adaptive configuration

Global defaults live on the hub; a zone may override them; an individual light may then be
calibrated on top. The first two layers define the *curve* — what "darkest" and "brightest"
mean across the day — while a light profile *clamps and calibrates* whatever the curve
produced.

Within a light profile the order is deliberate: the offset is applied first, then the
minimum and maximum. An offset says "this fixture reads dim"; a limit says "never below 15%,
it flickers". A calibration must never be able to breach a limit you set, so the limit wins.
A corollary worth knowing: per-light limits are absolute targets, not shifts of the zone's
range.

### Switches

A press turns the room on in adaptive mode; each further press moves along that switch's
list, wrapping back to adaptive. Two switches in one room can carry different lists, so the
one by the oven reaches *Cooking* first while the one by the door does something else.

Three ways a press can reach the integration, all of which work together:

| Binding | Use it for | Identifies the switch? |
|---|---|---|
| Watch an entity | Zigbee/Z-Wave buttons (`event`, `sensor`, `binary_sensor`) | **Yes** — this is what makes per-switch orders possible |
| `better_lighting.press` service | Automations, Node-RED, device triggers | Yes, by name |
| Plain `light.turn_on` on the room's light | A dumb wall switch, no configuration at all | No — Home Assistant only sees a service call |

A press that interrupts something automatic — a manual override, and later an insect scene
or a cinema mode — lands on adaptive *without* advancing; the next press then cycles
normally. That one rule is why there is no "user mode" with a timeout to expire.

Pressing the same button twice publishes the same value twice, which is not a state
*change*. Watching only for changes loses every second press, so the integration listens for
state reports as well.

### Presence and open windows

An optional occupancy sensor lights a room and darkens it again — but only when a configured
list of covers is closed, so a sunlit room is left alone. Closing a blind while somebody is
already in the room counts just as much as somebody walking into a dark one, which a
presence-only listener would miss entirely.

Presence can be silenced three ways, all meaning the same thing: night mode can ignore it
(the bedroom), an individual scene can ignore it (the living room during a film), and a room
a cross-zone mode is driving follows that mode's rules instead.

An optional door or window sensor switches the room to a designated **insect scene** —
usually deep amber, which attracts far fewer insects than white light. It will not light a
dark room, it debounces a slamming window, and a switch press waves it away until the window
is closed and opened again.

### Cross-zone modes

A **mode** spans several rooms and has named states — *playing*, *paused*, *credits* — that
an external automation moves between by setting one `select` entity. Each (state, room) pair
has a rule: apply a scene, switch the room off, return it to adaptive, or leave it alone.

Three things make this behave the way people actually expect:

- **An occupied room is not plunged into darkness.** The instruction waits for the room to
  empty, and is re-checked when it fires — by then the film may have ended, been paused, or
  the room may have been taken back, and each of those cancels it.
- **The snapshot is taken once**, when the session starts, and survives every state change
  within it. Re-snapshotting on *paused* would capture the film-watching state, and the
  restore at the end would relight nothing.
- **On the way out, rooms return to adaptive, filtered to the lights that were on
  beforehand.** The snapshot says *which* lights; the curve says *how bright*. Replaying
  stored brightness would immediately be corrected a tick later.

Press a switch in a room during the film and that room is yours for the rest of the session —
later state changes skip it, and the ending leaves it as you left it.

### A scene is a recipe, not a room

A scene says what a room should look like — a brightness, one colour, and which of those two
it takes over — with no reference to any particular light. That is what makes *Cooking* or
*Movie night* reusable everywhere rather than redefined per room.

Each scene chooses what it overrides, and **whatever it leaves alone keeps tracking the sun**:

| This scene sets | Brightness | Colour |
|---|---|---|
| Both | scene | scene |
| Brightness only | scene | **keeps adapting** |
| Colour only | **keeps adapting** | scene |
| Neither | keeps adapting | keeps adapting |

The two partial modes are the interesting ones. A *Cooking* scene can pin the brightness at
100% while the colour still warms through the evening. This works because the periodic
refresh only ever sends the axes the scene did not claim — so it never fights the scene, and
never stomps a relative dim layered on top of it.

### The zone entity is the input, not just the output

The render engine only ever commands the *member* lights — never the zone's own entity. So
every call arriving at the zone light is external by construction: a wall switch, a
dashboard, a voice assistant. That is what lets the integration tell a switch press from its
own output structurally rather than by guessing, and it means zero-flash turn-on needs no
patching of Home Assistant internals.

## When things go wrong

- **Diagnostics** (Settings → Devices & Services → Better Lighting → Download diagnostics)
  dump the configuration *and* the derived state: which axes each room thinks a human has
  taken over, which per-light calibrations are being clipped, what each mode session is
  waiting on. Those are what surprising behaviour usually turns on.
- **Repair issues** appear when configuration comes apart — a scene deleted while a switch
  still cycles through it, a room deleted from under a mode rule, a light claimed by two
  rooms. Home Assistant cannot refuse the deletion, so the runtime skips the broken reference
  rather than crashing, and says so instead of silently shortening your cycle.
- **Bus events** trace every decision: `better_lighting_press`,
  `better_lighting_zone_mode_changed`, `better_lighting_mode_changed`,
  `better_lighting_zone_opted_out`, `better_lighting_deferred_action`.

## Requirements

Home Assistant **2026.1 or newer**. This floor is verified, not assumed: the full test
suite is run against each release below.

| Home Assistant | Result |
|---|---|
| 2026.9.2 | pass |
| 2026.6.4 | pass |
| 2026.3.4 | pass |
| 2026.2.3 | pass |
| 2026.1.3 | pass |
| 2025.12.5 and earlier | not supported — see below |

Below 2026.1 the integration cannot currently be verified, for reasons that are *not* about
this code: 2025.12 and earlier ship a pytest whose assertion rewriting crashes on current
Python 3.13 patch releases, and 2025.3 pins a yanked `aiohttp` so it will not install at
all. Rather than claim compatibility that has not been demonstrated, the floor is set where
the evidence ends. `scripts/probe_ha_floor.sh` re-runs the matrix:

```bash
scripts/probe_ha_floor.sh 2026.1.3 2026.5.4 2026.9.2
```

Python 3.14 is required by HA 2026.3+; 2026.1 and 2026.2 still run on 3.13.

## Development

The system Python on many distributions is too old for modern Home Assistant, and may lack
`venv`. [uv] handles both:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.14
uv venv --python 3.14 .venv
uv pip install homeassistant pytest-homeassistant-custom-component ruff
```

[uv]: https://docs.astral.sh/uv/

```bash
.venv/bin/python -m pytest tests/          # everything
.venv/bin/python -m pytest tests/pure/     # no Home Assistant needed, milliseconds
.venv/bin/ruff check custom_components/ tests/
```

Tests are split deliberately:

- `tests/pure/` covers modules that import nothing from `homeassistant`. The calculation
  layer — curves, per-light overrides, the render pipeline, the state machine — lives there,
  so its full behaviour matrix is testable without an event loop.
- `tests/ha/` covers the integration surface: flows, entities, and service behaviour, using
  real member light entities so the assertions are about what the bulbs actually did.

## Licence

MIT.
