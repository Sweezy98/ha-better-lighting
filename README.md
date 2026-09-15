# Better Lighting

A Home Assistant integration that makes a light switch do the obvious thing.

Press it once and the room comes on in **adaptive mode** — brightness and colour temperature
following the sun. Press it again and it moves to the next scene in *that switch's* list.
Keep pressing and it comes back round to adaptive. The switch by the oven can reach *Cooking*
first while the one by the door does something else entirely.

Meanwhile: an automation can put the whole house into movie night and have it come back
correctly afterwards, presence sensors light rooms only when the blinds are down, an open
window switches to a deep amber that attracts fewer insects, and a bulb that reads dim can be
trimmed to match its neighbours.

It replaces the combination of [adaptive-lighting], [relative-light-group] and [scenery]
plus the automations needed to make them cooperate — and adds the things none of them can
express.

[adaptive-lighting]: https://github.com/basnijholt/adaptive-lighting
[relative-light-group]: https://github.com/Cheerpipe/relative-light-group
[scenery]: https://github.com/j9brown/scenery

> **Status: beta.** Everything described here is implemented and tested, but the integration
> has not yet been run on a live Home Assistant instance. Expect rough edges in the
> configuration screens, and please open an issue if you find one.

---

## Installation

### HACS

1. HACS → three-dot menu → **Custom repositories**
2. Add `https://github.com/Sweezy98/ha-better-lighting`, category **Integration**
3. Install **Better Lighting**, then restart Home Assistant
4. **Settings → Devices & Services → Add Integration → Better Lighting**

### Manually

Copy `custom_components/better_lighting` into your `config/custom_components/` directory and
restart.

**Requires Home Assistant 2026.1 or newer.** See [Supported versions](#supported-versions).

---

## Getting started

Adding the integration creates one **Better Lighting** entry that holds everything else. The
first screen asks for house-wide defaults — how bright and how warm your lights should be at
either end of the day. Sensible values are pre-filled; you can change them later.

Then, from the integration's page, add the pieces you need:

1. **A zone** — pick a room's lights. This is the only step you actually need. You get a
   `light.<room>` entity that follows the sun straight away.
2. **A scene or two** — *Cooking*, *Movie night*, *Reading*. Scenes are house-wide recipes,
   not per-room.
3. **A switch** — if you want a specific cycle order, or two switches in one room behaving
   differently.

A room with no switch configured still cycles: a plain `light.turn_on` on its light entity
walks through adaptive and then every scene, so a dumb wall switch works with no extra setup.

---

## What you configure

| Object | What it is |
|---|---|
| **Zone** | One room's lights, controlled together. Creates a `light`, a mode `select`, switches for adaptive and night, and buttons. |
| **Scene** | A reusable recipe — a brightness, one colour, and which of those it takes over. Not tied to a room. |
| **Light switch** | A physical switch, with its own ordered list of scenes. |
| **Light calibration** | Per-light minimum, maximum and offsets, to match a mismatched bulb to its neighbours. |
| **Mode** | A cross-zone mode such as Home Cinema: named states, and what each room does in each. |

### One light, one room

A light may belong to **exactly one zone**, and the configuration screen enforces it. That is
what keeps manual changes, scene control and switch presses unambiguous — the integration
always knows who owns a given bulb.

---

## The wall switch

A press turns the room on in adaptive mode; each further press moves along that switch's list,
wrapping back to adaptive. Three ways a press can reach the integration, and they work
together:

| How | Use it for | Can it tell switches apart? |
|---|---|---|
| **Watch an entity** | Zigbee/Z-Wave buttons (`event`, `sensor`, `binary_sensor`) | **Yes** — needed for per-switch orders |
| **`better_lighting.press`** | Automations, Node-RED, device triggers | Yes, by name |
| **Plain `light.turn_on`** on the room's light | A dumb wall switch, no configuration | No — Home Assistant only sees a service call |

A press that interrupts something automatic — a manual change, an insect scene, a film in
progress — lands on **adaptive without advancing**. The next press then cycles normally. So
grabbing the switch always gets you back to normal light in one press, whatever the house was
doing.

Quick taps are counted rather than queued or dropped: three taps move three places in one go,
without flashing through the two in between.

---

## Scenes

A scene says what a room should *look like*, with no reference to any particular light — which
is what makes *Cooking* reusable in every room rather than redefined in each.

Each scene chooses what it overrides, and **whatever it leaves alone keeps following the sun**:

| This scene sets | Brightness | Colour |
|---|---|---|
| Both | scene | scene |
| Brightness only | scene | **keeps adapting** |
| Colour only | **keeps adapting** | scene |
| Neither | keeps adapting | keeps adapting |

The two partial modes are the useful ones. A *Cooking* scene can pin the brightness at 100%
while the colour still warms through the evening.

Other options worth knowing:

- **Only affect lights that are already on** — adjust a room without lighting it up.
- **Lights this scene does not mention** — keep adapting (the default), switch off, or leave
  alone.
- **Ignore presence sensors while active** — for a film in the living room.

---

## Adaptive lighting

Three layers, each optional:

1. **House defaults** — set once when you add the integration.
2. **Per-room overrides** — a room can define its own curve.
3. **Per-light calibration** — trim one bulb.

The first two define the *curve*: what "darkest" and "brightest" mean across the day. A light
calibration *clamps and adjusts* whatever the curve produced.

Within a calibration the order matters, and it is deliberate:

> **The offset is applied first, then the minimum and maximum.**

An offset says "this fixture reads dim". A limit says "never below 15%, it flickers". A
calibration must never be able to breach a limit you set, so the limit wins. One corollary
worth knowing: per-light limits are **absolute targets**, not shifts of the room's range. With
a room value of 15%, an offset of +10 and a minimum of 30, the result is 30% — not 25%.

Values that get clipped are reported in the room's diagnostics rather than silently swallowed.

### Night mode

Night mode follows an **existing helper** — an `input_boolean`, a schedule, a sleep sensor —
rather than owning a schedule of its own, so the house keeps one source of truth for "we are
asleep". Each room can then either dim and warm, apply a designated night scene, or ignore
night entirely.

---

## Presence and open windows

An optional occupancy sensor lights a room and darkens it again, but only when a configured
list of covers is **closed** — so a sunlit room is left alone. Closing a blind while somebody
is already in the room counts just as much as somebody walking into a dark one.

Presence can be silenced three ways, all meaning the same thing:

- **Night mode ignores presence** — for a bedroom.
- **A scene ignores presence** — for the living room during a film.
- **A room a cross-zone mode is driving** follows that mode's rules instead.

It will also not switch off a room somebody has just set by hand.

An optional door or window sensor switches the room to a designated **insect scene** — usually
deep amber, which attracts far fewer insects than white light. It will not light a dark room,
it debounces a slamming window, and a switch press waves it away until the window is closed
and opened again.

---

## Cross-zone modes: home cinema

A **mode** spans several rooms and has named states — *playing*, *paused*, *credits* — that an
automation moves between by setting one `select` entity. Each (state, room) pair has a rule:
apply a scene, switch the room off, return it to adaptive, or leave it alone.

Three behaviours make this feel right rather than merely functional:

- **An occupied room is not plunged into darkness.** The instruction waits until the room
  empties, and is re-checked when it fires — by then the film may have ended or been paused,
  or you may have taken the room back, and each of those cancels it.
- **The snapshot is taken once**, when the session starts, and survives every state change
  within it — including a Home Assistant restart.
- **On the way out, rooms return to adaptive, relighting only the lights that were on
  beforehand.** The snapshot says *which* lights; the sun says *how bright*.

Press a switch in a room during the film and that room is **yours** for the rest of the
session: later state changes skip it, and the ending leaves it as you left it. Use
`better_lighting.rejoin_mode` to hand it back.

### The blueprint

A ready-made automation ships with the integration:

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FSweezy98%2Fha-better-lighting%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fbetter_lighting_home_cinema.yaml)

Point it at your media player and your mode's *State* entity. Playback starts the mode,
pausing moves it to *paused*, and stopping ends the session after a short delay — so switching
episodes does not relight the whole house.

---

## Services

| Service | What it does |
|---|---|
| `better_lighting.press` | Act as though a switch was pressed. `kind` can be `press`, `double_press` or `long_press`. |
| `better_lighting.cycle` | Move a room along its cycle, `next` or `previous`. |
| `better_lighting.set_adaptive` | Force a room back to adaptive, clearing any scene or manual change. |
| `better_lighting.activate_scene` | Apply a scene to a room. |
| `better_lighting.clear_manual_override` | Hand lights back to the adaptive engine. |
| `better_lighting.set_mode` | Move a cross-zone mode to one of its states. |
| `better_lighting.end_mode` | End a mode's session and put the rooms back. |
| `better_lighting.rejoin_mode` | Hand a room you took back to the mode again. |

Rooms can be targeted either by Home Assistant `target` (any of the room's entities) or by
naming the room in the `zone` field. The second is stabler for automations, since it survives
renaming an entity.

---

## When something surprises you

**Download diagnostics** — Settings → Devices & Services → Better Lighting → the three-dot
menu. It includes not just your configuration but what the integration currently *believes*:
which lights it thinks you have taken over by hand, which calibrations are being clipped, and
what each mode session is waiting on. Those answer most "why is this light doing that?"
questions immediately.

**Repair issues** appear when configuration comes apart — a scene deleted while a switch still
cycles through it, a room deleted from under a mode rule, a light claimed by two rooms. Home
Assistant cannot prevent the deletion, so the integration skips the broken reference rather
than failing, and tells you instead of silently shortening your cycle.

**Events** trace every decision, if you want to watch in Developer Tools:
`better_lighting_press`, `better_lighting_zone_mode_changed`, `better_lighting_mode_changed`,
`better_lighting_zone_opted_out`, `better_lighting_deferred_action`.

Common gotchas:

- **My lights stopped adapting.** Something was changed by hand, so the integration backed
  off. Press the switch, use the *Clear manual override* button, or wait for the timeout
  (90 minutes by default).
- **Presence does nothing.** Check the cover gate — by default *every* listed cover must be
  closed, and a cover Home Assistant cannot read counts as blocking.
- **A bulb drops its colour when brightness changes.** Some Tuya and older Zigbee bulbs do
  this. Turn on *Send brightness and colour separately* in the advanced settings.

---

## Supported versions

Home Assistant **2026.1 or newer**. This floor is verified rather than assumed — the full test
suite runs against each release below:

| Home Assistant | Result |
|---|---|
| 2026.9.2 | pass |
| 2026.6.4 | pass |
| 2026.3.4 | pass |
| 2026.2.3 | pass |
| 2026.1.3 | pass |
| 2025.12.5 and earlier | not supported |

Below 2026.1 the integration cannot currently be verified, for reasons that are not about this
code: 2025.12 and earlier ship a pytest whose assertion rewriting crashes on current Python
3.13 releases, and 2025.3 pins a yanked `aiohttp` so it will not install at all. Rather than
claim compatibility that has not been demonstrated, the floor is set where the evidence ends.

`scripts/probe_ha_floor.sh 2026.1.3 2026.9.2` re-runs the matrix.

---

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
.venv/bin/python -m pytest tests/pure/     # no event loop, milliseconds
.venv/bin/ruff check custom_components/ tests/
```

Tests are split deliberately:

- **`tests/pure/`** covers modules that import nothing from `homeassistant`. The calculation
  layer — sun curves, per-light calibration, the render pipeline, the cycling logic, the
  deferred-action rules — lives there, so its full behaviour matrix is testable without an
  event loop.
- **`tests/ha/`** covers the integration surface: flows, entities, services. It uses real
  member light entities and the real service path, so assertions are about what the bulbs
  actually did.

Note that `ruff`'s target is Python **3.13**, not 3.14, because Home Assistant 2026.1 and
2026.2 run on 3.13. Targeting the development Python once let 3.14-only syntax reach a release
that could not parse it.

## Licence

MIT. See [LICENSE](LICENSE).
