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

> **Status: early development.** Milestones 1 and 2 of 8 are complete. Zones exist as light
> groups with relative dimming and on-state memory, they follow the sun, night mode tracks a
> helper entity, and individual lights can be calibrated with offsets and limits. Scenes,
> switch cycling, presence and the cross-zone cinema modes are not implemented yet.

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

### The zone entity is the input, not just the output

The render engine only ever commands the *member* lights — never the zone's own entity. So
every call arriving at the zone light is external by construction: a wall switch, a
dashboard, a voice assistant. That is what lets the integration tell a switch press from its
own output structurally rather than by guessing, and it means zero-flash turn-on needs no
patching of Home Assistant internals.

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
