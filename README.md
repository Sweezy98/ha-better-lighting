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

> **Status: early development.** Milestone 1 of 8 is complete: zones exist as light groups
> with relative dimming and on-state memory. The adaptive engine, scenes, cycling, presence
> and cross-zone modes are not implemented yet.

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

### The zone entity is the input, not just the output

The render engine only ever commands the *member* lights — never the zone's own entity. So
every call arriving at the zone light is external by construction: a wall switch, a
dashboard, a voice assistant. That is what lets the integration tell a switch press from its
own output structurally rather than by guessing, and it means zero-flash turn-on needs no
patching of Home Assistant internals.

## Requirements

Home Assistant **2026.1+** (developed and tested against 2026.9.2; lower versions are
untested). Python 3.14, as required by HA 2026.x.

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
