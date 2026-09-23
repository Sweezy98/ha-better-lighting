# Better Lighting — user guide

How to set a house up, in the order that works: one room first, then the
things that make it behave on its own. Every step is optional except the
first. Nothing here needs YAML.

---

## Before you start

- **Home Assistant 2026.1 or newer.**
- **Your lights already work in Home Assistant.** This integration drives
  existing light entities; it does not talk to bulbs itself.
- **The `recorder` integration**, if you want the presence simulation to
  replay what your house actually did. Everything else works without it.
- **Decide nothing in advance.** Every setting can be changed later, and
  changing one never loses your scenes.

---

## Step 1 — Add the integration

**Settings → Devices & Services → Add integration → Better Lighting.**

You are asked for house-wide defaults: how bright and how warm your lights
should be at the darkest point of the day and at the brightest. The
pre-filled values suit most houses. These are the *curve*: what "darkest"
and "brightest" mean here.

You get one **Better Lighting** entry and a **Better Lighting** item in the
sidebar. Almost everything after this happens in that panel.

> Only one entry exists, ever. Everything else lives inside it.

---

## Step 2 — Your first room

**Panel → Rooms → Add a room.** Give it a name, an icon, and its lights.

That is the whole minimum. You now have:

| Entity | What it does |
|---|---|
| `light.<room>` | The room, as one light. Dimming it dims the room *relatively*: a lamp already at 20% and a downlight at 80% both come down, keeping their difference. |
| `select.<room>_scene` | Which scene the room is showing, or *Adaptive*. |
| `switch.<room>_adaptive` | Whether the sun is driving this room. |
| `switch.<room>_night` | Night mode for this room. |
| buttons | Next scene, previous scene, back to adaptive, clear manual control. |

Turn the room on. It follows the sun from this moment, with no further
setup.

### One light, one room

A light belongs to **exactly one room**, and saving is refused if two rooms
claim the same bulb. This is not a limitation to work around — it is what
lets the integration know, without guessing, who owns a bulb when somebody
presses a switch.

If a bulb is genuinely shared between two spaces, make it one room and use
**zones** (step 10) for the parts.

---

## Step 3 — Look at the curve

**Panel → Diagnostics.** Pick the room. You get the day drawn out: a line
for brightness, a band behind it coloured with the colour temperature at
each moment, and markers for sunrise, sunset and now. Underneath is what
each light would be sent *this second*, after that room's offsets and
clamps.

This is the fastest way to answer "the evening feels too bright". Change the
room's adaptive settings, look again.

### Which setting wins

Three layers, each narrowing the one above:

1. **Hub defaults** — the shape of the day for the whole house.
2. **Room overrides** — this room is dimmer, or warmer, or starts fading
   earlier.
3. **Light calibration** — this *bulb* reads low, or must never go below 15%.

Hub and room minimum/maximum define the *curve*. Calibration minimum/maximum
is a *clamp* applied afterwards. That difference matters: the curve says what
the day looks like, the clamp says what a particular fixture can survive.

### Brightness shapes

- **tanh** (the default for new rooms) starts fading *before* sunset, so the
  evening arrives gradually.
- **sun** follows the sun's height directly.

Try tanh first. If the house goes dim too early for you, move to sun.

---

## Step 4 — Make mismatched bulbs match

One fitting with a filament bulb and an LED strip never looks even. **Room →
Light calibration → Add.**

| Setting | Use it when |
|---|---|
| **Brightness offset** (percentage points) | This bulb reads low. `+10` lifts it everywhere. |
| **Colour temperature offset** (Kelvin) | This bulb runs cold compared to its neighbour. |
| **Minimum / maximum brightness** | Below 15% it flickers; above 80% it is unbearable. |

The offset is applied **before** the clamp, deliberately: a calibration must
never breach an operating limit. If a value gets clipped, it is reported in
the room's diagnostics rather than silently swallowed.

---

## Step 5 — Scenes

A scene is **a list of this room's lights and what each should look like**.
Scenes belong to a room: a reading scene for the living room and one for the
bedroom are different lists of different lights.

**Panel → the room → Scenes → Add.**

Two ways to build one:

- **By hand.** Pick lights, set brightness and colour on each. The room
  changes as you drag — this is why scenes have a page rather than a form.
- **Capture the room as it is now.** Get the room right by any means you
  like, then save what you see.

Each light in a scene can be set to:

| Choice | Result |
|---|---|
| Brightness and colour | Exactly this. |
| **Brightness only** | This bright, colour still following the sun. |
| **Colour only** | This colour, brightness still following the sun. |
| **Off** | Off while this scene is showing. |
| **Leave alone** | Untouched — whatever it was doing, it carries on. |

*Brightness only* and *colour only* are the ones people miss. A film scene
that fixes the colour and lets the brightness keep tracking the day is a
different, better thing from one that freezes both.

### Lights the scene does not mention

Each scene says what happens to the rest of the room: leave them, turn them
off, or put them back on the curve.

### Colour presets

**Global settings → Colour presets.** Name a colour once — "TV orange",
"candle" — and pick it by name in every scene. Change the preset, and every
scene using it changes.

### Hiding a scene

A scene can be left out of a switch's cycle (step 6) and out of a dashboard
card's menu (step 14) without deleting it. Useful for scenes that only an
automation ever sets.

---

## Step 6 — Wall switches

A room with no switch configured **already cycles**: a plain `light.turn_on`
on its light entity walks through adaptive and then every scene. A dumb wall
switch works with no setup at all.

Configure a switch when you want more than that:

- **A specific order.** This switch goes adaptive → cooking → off, and skips
  the film scene.
- **Two switches in one room, behaving differently.** The door switch cycles
  everything; the bedside switch goes straight to *Night*.
- **A rocker.** The lower half gets its own actions, and holding either end
  dims or brightens.
- **A switch that drives only a zone** (step 10) — the desk's switch lights
  the desk and leaves the couch alone.

**Panel → the room → Light switches → Add.** Bind it to the entity your
switch already produces (an `event`, a `binary_sensor`, a `sensor`, a
`switch` or an `input_button`), then build its ordered list.

### What a press means

A press is not just "next". It **dismisses whatever was overriding the room**
and then advances. If a house mode is driving the room, the first press takes
the room back without advancing — so pressing once gets you out of the film,
and pressing again starts cycling normally. You never have to press twice for
no visible reason.

---

## Step 7 — Night mode

Night mode follows **one helper for the whole house** — a `binary_sensor`,
an `input_boolean` or a `schedule` you already have. Set it in **Global
settings**.

Each room then decides for itself what that means:

| Room behaviour | What happens at night |
|---|---|
| **Minimum settings** | Brightness clamped down, colour warmed. |
| **A scene** | This room shows its night scene. |
| **Off** | Night mode does nothing here. |

`switch.<room>_night` mirrors the helper and can also be flipped by hand;
the helper wins again the next time it changes.

**Ignore presence at night** is the bedroom setting: motion at 3am should
not light the room.

The card shows a moon badge while a room is actually in night mode — which
is not the same as the helper being on, since a room set to *Off* says no.

---

## Step 8 — Motion and door sensors

Every room **and** every zone has its own triggers, set where you are already
looking at that room. **Panel → the room → Triggers.**

### Triggers are ORed

A drive has motion, a garage has a door, a porch may have both. Any one of
them is enough:

- **While any trigger is active**, the lights stay on.
- **When the last one clears**, they go off after the delay you set.
- **A fresh trigger during the wait starts the wait again.**

"Active" needs no configuring for the usual sensors: `on`, `open`, `home` and
`detected` all count, so a binary sensor, a cover and a device tracker each
work as they are. Name a state outright for anything that reports something
else.

### The switch beats the timer

If somebody reached for the switch — before a sensor ever fired, or half way
through the wait — **the clock stops**. The lights stay on until they are
turned off by hand, and then the automation has them back automatically. You
never have to re-enable anything.

Turn off **Hold when set by hand** if you would rather the timer always won.

The card shows a hand badge while a room is being held this way, and a
countdown while the timer is running.

### Lighting a whole room versus a zone

- A **room** lights from its triggers through its **Presence** settings
  (step 11).
- A **zone** does it with *Light this zone while the sensor is active* —
  which is how one motion sensor lights the drive without touching the rest
  of the outside.

---

## Step 9 — Rules

Rules decide **whether a trigger may fire**. They belong to the same room or
zone as the triggers they gate.

| Rule | Checks |
|---|---|
| **Between two times** | The local clock. An end earlier than the start runs through midnight: 22:00 until 06:00 is one night. |
| **A number below / above a threshold** | A lux sensor, a temperature, anything numeric. |
| **An entity in a particular state** | A helper that arms the automation, a holiday toggle that disarms it, or `sun.sun` being `below_horizon` — which is "after dark" without owning a lux sensor. |

### They are ANDed

Every rule has to hold. Adding a rule can only ever make an automation fire
**less** often — the opposite of adding a trigger, which can only make it fire
more. If something is not firing, look at the rules; if something fires too
much, look at the triggers.

### The day strip

Any list of rules is drawn as a **bar under it** — green where the clock
leaves the automation open, red where it does not, with the times labelled.
A window through midnight reads as the two ends of one night rather than as a
gap. Only the clock can be drawn that way; a lux threshold still applies on
top of it.

### Unreadable sensors block by default

"Allowed only if every rule passes" — and a rule nobody can evaluate has not
passed. Each rule can be told to let it through instead, which is what you
want for a flaky sensor that should not leave somebody on an unlit path.

### A recipe: the porch

- Trigger: the motion sensor by the door.
- Rule: `sun.sun` is `below_horizon`.
- Rule: between 16:00 and 01:00.
- Turn-off delay: 2 minutes.

Nothing happens in daylight, nothing happens at four in the morning, and a
visitor at eleven gets two minutes of light per movement.

---

## Step 10 — Zones: the desk that does not go dark for the film

A living room is one room and several places. The couch and the desk are lit
together, switched together and adapt together — which is what makes them one
room — but a desk somebody is working at should not be darkened because the
rest of the room is watching a film.

A zone **follows its room until it has a reason not to**, and rejoining is
the default.

**Panel → the room → Zones → Add.** Give it the lights that belong to it.

What a zone can be given:

- **An occupancy sensor and *step out of a house mode while occupied*.**
  While somebody is there **and** a cross-room mode is driving the room, the
  zone carries on by itself. A desk already occupied when the film starts is
  never darkened in the first place.
- **A rejoin delay.** When the sensor has been clear that long, the zone goes
  back to whatever the room is being told. Nothing is put back by hand.
- **Its own switch.** A switch can name a zone and then drives only that
  zone. Stepping it back to adaptive puts the zone back in the room.
- **Its own adaptive curve.** Brighter and cooler than the room around it.
  One more layer of the same kind: hub, then room, then part of the room.
- **Its own triggers and rules**, exactly as a room has.

Occupancy on its own detaches nothing: with no mode driving the room, a busy
desk is the room's own business, which is what the room's presence settings
are for. A light may be in at most one zone, for the same reason it is in at
most one room.

---

## Step 11 — Presence for a whole room

**Panel → the room → Presence.** This is what the room's triggers do to the
room as a whole.

| Setting | Use it for |
|---|---|
| **What happens when somebody arrives** | Adaptive, a specific scene, or nothing. |
| **What happens when the room clears** | Off, back to adaptive, or nothing. |
| **Only when the lights are off** | Do not interrupt a room somebody has already set. |
| **The cover gate** | Only light this room when its blinds are closed — "it is dark in here" without a lux sensor. An unreadable cover blocks by default; that is configurable. |

---

## Step 12 — Open windows

**Panel → the room → Open windows.** Point it at the window sensors of that
room and pick what an open window does — usually an amber, insect-friendly
look.

It **outranks a house mode**, because an open window is a physical fact about
this room while a mode is a house-wide preference. It still yields to a
switch press: press, and it is dismissed until the window closes and is
opened again. No timer is involved.

---

## Step 13 — House-wide modes: home cinema

A mode is **one thing that changes several rooms at once**, with named states.

**Panel → Modes → Add.** Name it, name its states — `playing`, `paused`,
`ended` — then say what each room does in each state.

You drive it from `select.<mode>_state`. That is the whole integration point:
your media player automation calls `select.select_option`, and nothing else
needs to know anything.

What the mode handles for you:

- **A snapshot** of every room it touches, taken **once** when the mode
  starts and kept across state changes. (Re-snapshotting on `paused` would
  capture the film's own lighting, and the final restore would turn nothing
  back on.)
- **Restoring on exit** to *adaptive, filtered to the lights that were on*.
  The snapshot supplies which lights; the curve supplies how bright.
  Restoring last hour's exact brightness would immediately fight the sun.
- **A press opts a room out.** Somebody who reaches for the kitchen switch
  mid-film keeps the kitchen. It is not put back at the end either.
- **Deferred actions.** "Turn the kitchen off when it is empty" waits for the
  kitchen to actually empty, then checks the film is still running and
  nobody has taken the room over, and only then acts.

A mode can carry its own **rules**, the same rules a trigger uses.

---

## Step 14 — Presence simulation

While the house is empty, make it look lived in.

### The one thing you must get right

Point the integration at **one helper that says nobody is home** — **Global
settings → Presence simulation**. Deliberately *not* worked out from device
trackers: a guest nobody tracks is still somebody at home, and simulating
over their head is the one failure here that actually matters. An unreadable
helper counts as *not* empty.

### What each room does

| Mode | What happens |
|---|---|
| **Replay what it actually did** | The same weekday, a week ago, from the recorder — shifted. This is the convincing one. |
| **Light it adaptively** | Steady, and obviously so. |
| **Show a scene** | One fixed look. |
| **Take no part** | Nothing. A bathroom nobody can see from the street proves nothing. |

Replay matters more than it sounds. A hall light that comes on at 19:03:12
every single evening advertises an empty house rather than hiding one — so
**each step moves by its own random amount**, up to the jitter you set.
Shifting the whole evening by the same twelve minutes would still be exactly
last week. Order survives the shuffle: a light still comes on before it goes
off. Only *which rooms were lit when* is replayed; brightness comes from the
room's own curve.

### Rules, and per-room rules

The same rules a trigger uses gate the whole thing. House-wide rules are in
**Global settings → Presence simulation**. A room can add its own on its own
simulation screen, and **both have to hold** before that room takes part.

A room can also be told to sit it out **while its blinds are closed**, since
a lit room nobody can see proves nothing.

### Testing it without waiting for nightfall

`switch.better_lighting_presence_simulation` shows whether one is running and
which rooms are in it. **Turning it on runs one regardless of the rules** —
which is how you check the setup in the middle of the afternoon.

It stops the instant the helper says somebody is back — not at the end of the
timeline — and every room it lit goes off again, unless somebody reached for
a switch on their way in.

Replay needs the `recorder`. Without it, that mode finds nothing to play and
leaves the room dark, which is a better impression of an empty house than
lighting it all evening.

The card shows a clock-house badge while a room is taking part.

---

## Step 15 — Light groups

Three smart bulbs in one fitting should change together. Asking Home
Assistant to set three entities sends three Zigbee commands and you can watch
them arrive; a Zigbee group entity is one multicast.

**Panel → the room → Light groups → Add.** Name the group, list its members,
and point it at the group entity your Zigbee integration already exposes.

The group is used whenever every member is being told the same thing, and
abandoned the moment one differs — so a scene with one red, one green and one
blue bulb still addresses them individually.

Groups can contain groups. A 3×3 of ceiling spots is three rows of three, and
a scene can set the whole ceiling amber and the middle row dim in two lines
rather than nine. A light's own entry beats every group holding it. A group
cannot contain itself, and saving one that does is refused with the loop
named.

---

## Step 16 — The dashboard card

**Edit dashboard → Add card → Better Lighting room.** Pick the room's light.
There is no resource to add; the card is loaded for you.

One row of controls each:

- **The title** opens Home Assistant's own light dialog, which is where
  colour and temperature live.
- **Badges** say *why* the room looks the way it does — presence, night mode,
  presence simulation, on by hand or automatically, and a countdown when the
  room is due to switch itself off. They are all the same grey: none of them
  is a thing to press.
- **Back to adaptive** appears only when the room is *not* adaptive. It is
  the way back after a scene or a manual change, not a toggle.
- **Power**, and a **brightness bar** that moves the room the way the room is
  configured to move. It works on a room that is off, too: setting a
  brightness there lights it adaptively at that brightness, with the colour
  still following the sun.
- **Scenes** as previous, a menu, and next.
- **One extra button**, optional, for something that is not about this room —
  a house mode, a helper, the cinema. Any entity, wearing that entity's icon
  unless you choose another.

In the card's settings you can also **hide scenes**: hidden scenes are left
out of the menu *and* skipped by the arrows. A hidden scene that something
else turns on is still named on the dropdown — it just never appears in the
list.

---

## Step 17 — The control panel

**Panel → Control.** Every room as the card above, plus the things that would
otherwise need a dashboard: the presence simulation toggle, the back-to-night
request, and every house mode. Useful before you have built a dashboard, and
useful for testing a setup from the page that configured it.

---

## Automations and services

You rarely need these — a room's own entities cover most of it — but they are
there.

| Service | What it does |
|---|---|
| `better_lighting.press` | Simulate a switch press, naming the switch. |
| `better_lighting.set_adaptive` | Put a room back on the curve. |
| `better_lighting.clear_manual` | Forget that a light was set by hand. |

For a mode, call `select.select_option` on `select.<mode>_state`. For a
scene, call `select.select_option` on `select.<room>_scene`.

---

## When something surprises you

**Panel → Diagnostics** is the first stop. It shows what every room and mode
currently believes — its mode, its scene, which lights are under manual
control, whether night or open-window is showing, which mode session owns it
— beside a live log of the events that got it there. The room's state says
where it ended up; the log says which press or which film put it there.

It is the same data the diagnostics download produces, so the page and your
bug report cannot disagree.

| Symptom | Look at |
|---|---|
| **A switch does nothing.** | Diagnostics shows what the switch published and what was made of it. A value that does not match any configured action is the usual answer. |
| **A room will not respond to motion.** | The rules. They are ANDed, and an unreadable sensor blocks by default. |
| **A room will not go back to automatic.** | Somebody held it by hand. Turn it off once, or press *Back to adaptive*. |
| **A light is on but Home Assistant shows it off.** | That is a bulb-level problem; the room believes what Home Assistant tells it. |
| **The dashboard says the card does not exist.** | The page you are on was served before the integration was set up, or your browser is holding an old copy. Use **Reload the frontend** in the panel's three-dot menu — it clears only what can be stale. |
| **Changes in the panel do not show.** | Same button. |

---

## One evening, put together

- 16:30. The sun drops; every room warms and dims on its own curve. Nobody
  did anything.
- 18:00. The porch sensor sees somebody. The rules pass — it is after dark,
  it is before one in the morning — so the porch lights for two minutes.
- 20:30. The film starts. An automation sets `select.home_cinema_state` to
  `playing`. The living room dims to its film scene; the kitchen is asked to
  go off but is occupied, so the request waits. The desk in the corner is
  occupied and steps out of the mode: it stays as it was.
- 20:35. The kitchen empties. The deferred action checks the film is still
  running and nobody has taken the kitchen over, and turns it off.
- 21:10. Somebody presses the kitchen switch for a glass of water. The
  kitchen opts out of the film and cycles normally from adaptive.
- 22:00. The night helper comes on. The bedroom clamps to its minimum and
  warms; the living room ignores it, because it is set to.
- 23:30. The film ends. Every room the mode touched goes back to adaptive,
  filtered to the lights that were on when it started — at the brightness the
  curve says half past eleven should be, not the brightness of half past
  eight. The kitchen, which opted out, is left exactly as the person who
  opted it out left it.
