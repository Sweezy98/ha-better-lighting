"""Turning a real light switch into a press.

Three bindings, all of which coexist:

* **entity_state** -- we watch an entity ourselves. This is the only binding
  that can tell *which* switch was pressed, so it is what makes requirement 8
  (per-switch cycle orders) possible at all.
* **service_only** -- driven by ``better_lighting.press`` from an automation.
* **zone_light** -- a bare ``light.turn_on`` on the zone's own light entity.
  Works with a plain wall switch and no configuration, but Home Assistant only
  sees a service call, so it cannot identify the switch; it is attributed to
  the zone's default controller.

The awkward part is not detecting a press. It is *not* detecting one when
Home Assistant restarts, when an entity recovers from unavailable, or when our
own command echoes back. Each of those looks exactly like a button push if you
only compare two states.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    EventStateReportedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_state_report_event,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from .const import DOMAIN, BindingType, PressAction

if TYPE_CHECKING:
    from .context import ContextRegistry
    from .models import ControllerConfig
    from .zone import ZoneController

_LOGGER = logging.getLogger(__name__)

EVENT_PRESS = f"{DOMAIN}_press"

# An `event` entity's state is the timestamp it last fired. Some integrations
# restore that across a restart, so freshness -- not "did the state change" --
# is what actually distinguishes a real press.
MAX_EVENT_AGE = 30.0

# Presses arriving before this much of Home Assistant's startup has passed are
# replayed history, not fingers on switches.
STARTUP_GRACE = 15.0

# What a second quick press means, for a button that has no double press of
# its own. Only these two: a long press is already a gesture of its own, and
# a double that the button *did* report needs no help.
DOUBLE_OF = {"press": "double_press", "down_press": "down_double_press"}

# How many of a switch's recent publications to keep for the diagnostics page.
# Enough to see a press and the value the device clears itself to afterwards.
SEEN_HISTORY = 8

# What the classifier calls a finger coming off the button. Not a press, and
# never dispatched as one: its only job is to end a ramp.
RELEASE = "release"

# A ramp that never hears a release stops by itself. Ten seconds is long past
# any deliberate hold -- a full sweep of the room takes about four at the
# default step -- and is there for buttons that report holding but not
# letting go.
MAX_RAMP_SECONDS = 10.0

# The holds that mean "keep going": the ones that move the room by a step,
# rather than the ones that reset it or switch it off.
RAMPING_ACTIONS = frozenset({PressAction.BRIGHTEN, PressAction.DIM})


@dataclass(slots=True)
class PressBurst:
    """Taps being held back to see whether a second one arrives.

    Only ever used by a switch whose button cannot report a double press of
    its own: every other press is acted on the moment it arrives.
    """

    count: int = 0
    kind: str = "press"
    cancel: CALLBACK_TYPE | None = None


@dataclass(slots=True)
class HoldRamp:
    """A button being held down, and the repeat that keeps the room moving."""

    kind: str | None = None
    cancel: CALLBACK_TYPE | None = None
    # When to give up on hearing a release. Pushed forward by every repeat of
    # the hold, so a button that keeps saying so is not cut off mid-gesture.
    expires: float = 0.0


@dataclass(slots=True)
class ControllerRuntime:
    """One configured switch, watching whatever it is bound to."""

    hass: HomeAssistant
    config: ControllerConfig
    zone: ZoneController
    contexts: ContextRegistry

    _unsubscribers: list[CALLBACK_TYPE] = field(default_factory=list)
    _burst: PressBurst = field(default_factory=PressBurst)
    _ramp: HoldRamp = field(default_factory=HoldRamp)
    _started_at: float = 0.0
    # What this switch has published lately and what each value was read as.
    # The one question the logs could never answer: a value nobody recognises
    # produces no press, and therefore no trace of itself.
    seen: list[dict[str, Any]] = field(default_factory=list)

    # -- lifecycle ---------------------------------------------------------

    async def async_setup(self) -> None:
        binding = self.config.binding_type
        if binding is not BindingType.ENTITY_STATE or not self.config.binding_entity:
            return

        @callback
        def _started(_hass: HomeAssistant) -> None:
            # Registering only once Home Assistant has started means the burst
            # of restore-driven state writes never reaches us at all, which is
            # a far more reliable guard than trying to recognise them.
            self._started_at = time.monotonic()
            self._unsubscribers.append(
                async_track_state_change_event(
                    self.hass, [self.config.binding_entity], self._handle_state
                )
            )
            # Pressing the same button twice publishes the same value twice,
            # which is not a state *change* -- Home Assistant reports it
            # instead. Without this, every second identical press is silently
            # lost, which is the single most common way switch handling goes
            # wrong.
            self._unsubscribers.append(
                async_track_state_report_event(
                    self.hass, [self.config.binding_entity], self._handle_report
                )
            )

        self._unsubscribers.append(async_at_started(self.hass, _started))

    @callback
    def async_shutdown(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        if self._burst.cancel is not None:
            self._burst.cancel()
            self._burst.cancel = None
        self._stop_ramp()

    # -- press detection ---------------------------------------------------

    @callback
    def _note(self, value: str | None, kind: str | None) -> None:
        """Remember what the switch said, and what we made of it."""
        if value is None:
            return
        self.seen.insert(
            0,
            {
                "at": dt_util.utcnow().isoformat(),
                "value": value,
                "read_as": kind or "nothing",
            },
        )
        del self.seen[SEEN_HISTORY:]

    @callback
    def _handle_state(self, event: Event[EventStateChangedData]) -> None:
        if self.contexts.is_ours(event.context):
            return
        kind = self.classify(event.data.get("old_state"), event.data["new_state"])
        self._note(self._seen_value(event.data["new_state"]), kind)
        if kind is None:
            return
        self.async_press(kind)

    @callback
    def _seen_value(self, state: State | None) -> str | None:
        """The word a state published, for the record.

        Deliberately not ``_press_value``: that one withholds a stale
        timestamp, and a value withheld is exactly what somebody staring at
        an unresponsive switch needs to be told about.
        """
        if state is None:
            return None
        attribute = self.config.press_attribute
        if attribute and (value := state.attributes.get(attribute)) is not None:
            return str(value)
        return state.state

    @callback
    def _handle_report(self, event: Event[EventStateReportedData]) -> None:
        """A repeat of the value the entity already had."""
        new = event.data["new_state"]
        if new is None or self.contexts.is_ours(event.context):
            return
        # There is no "old" here by definition, so the transition guards do not
        # apply; freshness is what separates a real repeat press from noise.
        value = self._press_value(new)
        kind = self._kind_for(value) if value is not None else None
        if kind is None and value is not None and self.config.any_change_is_a_press:
            kind = "press"
        self._note(self._seen_value(new), kind)
        if kind is not None:
            self.async_press(kind)

    def _kind_for(self, value: str) -> str | None:
        """Which of the seven things this word means, if any.

        The lower half is checked first: a rocker that publishes "off" for its
        down button would otherwise be read as an ordinary press, since "off"
        is also in the default single-press vocabulary.
        """
        config = self.config
        if value in config.release_states:
            return RELEASE
        if value in config.down_long_press_states:
            return "down_long_press"
        if value in config.down_double_press_states:
            return "down_double_press"
        if value in config.down_press_states:
            return "down_press"
        if value in config.long_press_states:
            return "long_press"
        if value in config.double_press_states:
            return "double_press"
        if value in config.press_states:
            return "press"
        return None

    def classify(self, old: State | None, new: State | None) -> str | None:
        """Decide whether this state change is a press, and of what kind."""
        if new is None or new.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        if old is None:
            # The entity has only just appeared; there is nothing to compare.
            return None
        if old.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            # Recovering from unavailable is not somebody pressing a button.
            return None
        if (
            self._started_at
            and time.monotonic() - self._started_at < STARTUP_GRACE
            and not self._is_fresh(new)
        ):
            return None

        value = self._press_value(new)
        if value is None:
            return None

        if (kind := self._kind_for(value)) is not None:
            return kind

        # A numeric counter (the z2m "action counter" pattern) publishes an
        # ever-increasing number rather than a word; any change is one press.
        if _is_number(value) and _is_number(old.state) and value != old.state:
            return "press"
        if self.config.any_change_is_a_press:
            # A toggle alternating on and off, or any button whose vocabulary
            # we have none of: getting here means something changed, it was
            # not us, and the entity is neither unavailable nor stale. For
            # this switch, that is the whole definition of a press.
            return "press"
        return None

    def _press_value(self, state: State) -> str | None:
        """The word this state is publishing, if any."""
        attribute = self.config.press_attribute
        if attribute and (value := state.attributes.get(attribute)) is not None:
            # `event` entities keep the interesting part in an attribute; the
            # state itself is only a timestamp.
            if not self._is_fresh(state):
                return None
            return str(value)
        return state.state

    @staticmethod
    def _is_fresh(state: State) -> bool:
        """For timestamp-valued entities, did this just happen?"""
        try:
            fired = dt_util.parse_datetime(state.state)
        except (TypeError, ValueError):
            return True
        if fired is None:
            return True
        return abs((dt_util.utcnow() - fired).total_seconds()) <= MAX_EVENT_AGE

    # -- dispatch ----------------------------------------------------------

    def _pairing(self, kind: str) -> tuple[bool, float]:
        """Whether two taps of this kind mean a double, and how long to wait.

        Each half of a rocker answers for itself: a switch can perfectly well
        report a double on its upper button and not on its lower one.
        """
        if kind not in DOUBLE_OF:
            return False, 0.0
        if kind == "down_press":
            return (
                self.config.down_double_from_two_presses,
                self.config.down_double_press_window_ms / 1000,
            )
        return (
            self.config.double_from_two_presses,
            self.config.double_press_window_ms / 1000,
        )

    @callback
    def async_press(self, kind: str = "press") -> None:
        """Accept a press and act on it.

        At once, in the ordinary case: a switch that reports its own gestures
        has told us everything already, and waiting to see whether more taps
        arrive bought nothing but a lag on every single press. Three quick
        taps move three places, one at a time.

        Nothing is dropped for arriving too soon after the last one, either.
        A guard against a device delivering the same press twice also ate the
        second half of a deliberate double tap, and the honest reading of two
        presses that close together is two presses -- or, for a switch that
        says so, the double press its button cannot send.

        The exception is a button that cannot report a double press and sends
        the same single press twice instead. There the gesture genuinely is
        not known until the window closes, so those taps -- and only those --
        are held back.
        """
        if kind == RELEASE:
            # Not a press. The finger has come off, which is only ever the end
            # of something that was already running.
            self._stop_ramp()
            return

        if self._ramp.kind is not None and kind == self._ramp.kind:
            # A device that repeats its hold while the button is down, rather
            # than saying it once. Evidence that the finger is still there,
            # not another step: taking both would dim at twice the speed on
            # one device and the right speed on the other.
            self._extend_ramp()
            return

        pairing, window = self._pairing(kind)
        if not pairing or not window:
            self._fire(kind)
            self._flush_now(kind, 1)
            self._maybe_ramp(kind)
            return

        self._burst.count += 1
        self._burst.kind = kind
        if self._burst.cancel is not None:
            self._burst.cancel()

        @callback
        def _flush(_now) -> None:
            self._burst.cancel = None
            count, self._burst.count = self._burst.count, 0
            # Anything past the second tap is part of the same gesture: a
            # finger that lands three times inside the window meant one
            # thing, and guessing which is worse than doing the plain thing.
            gesture = DOUBLE_OF[kind] if count >= 2 else kind
            self._fire(gesture)
            self._flush_now(gesture, 1)

        self._burst.cancel = async_call_later(self.hass, window, _flush)

    # -- holding ------------------------------------------------------------

    def _hold_action(self, kind: str) -> PressAction | None:
        """What this hold does, if it is a hold at all."""
        if kind == "long_press":
            return self.config.long_press_action
        if kind == "down_long_press":
            return self.config.down_long_press_action
        return None

    @callback
    def _maybe_ramp(self, kind: str) -> None:
        """Keep a held dimmer moving until the finger comes off.

        Almost every button says "held" once and then nothing at all until it
        says "released", so a hold that dims by one step and stops is not a
        dimmer -- it is a press with a long name. The repeating has to come
        from this side.
        """
        if not self.config.hold_ramp or not self.config.hold_interval_ms:
            return
        if self._hold_action(kind) not in RAMPING_ACTIONS:
            # A hold that resets the room or switches it off means one thing
            # and should happen once.
            return

        self._stop_ramp()
        self._ramp.kind = kind
        self._ramp.expires = time.monotonic() + MAX_RAMP_SECONDS
        interval = self.config.hold_interval_ms / 1000

        @callback
        def _step(_now) -> None:
            if time.monotonic() > self._ramp.expires:
                # The release never came. Some buttons report holding and not
                # letting go, and a room that dims for ever is worse than one
                # that stops early.
                _LOGGER.debug(
                    "%s: no release after %.0fs; ending the ramp",
                    self.config.name,
                    MAX_RAMP_SECONDS,
                )
                self._stop_ramp()
                return
            self._flush_now(self._ramp.kind or kind, 1)
            self._ramp.cancel = async_call_later(self.hass, interval, _step)

        self._ramp.cancel = async_call_later(self.hass, interval, _step)

    @callback
    def _extend_ramp(self) -> None:
        """A repeated hold is the same hold, held for longer."""
        self._ramp.expires = time.monotonic() + MAX_RAMP_SECONDS

    @callback
    def _stop_ramp(self) -> None:
        if self._ramp.cancel is not None:
            self._ramp.cancel()
        self._ramp.cancel = None
        self._ramp.kind = None

    @callback
    def _fire(self, kind: str) -> None:
        """Tell the bus, so an automation can hear the press too."""
        self.hass.bus.async_fire(
            EVENT_PRESS,
            {
                "controller_id": self.config.subentry_id,
                "controller": self.config.name,
                "zone_id": self.config.zone_id,
                "kind": kind,
            },
        )

    @callback
    def _flush_now(self, kind: str, steps: int) -> None:
        self.hass.async_create_task(
            self.zone.async_press(self.config, kind, steps=max(steps, 1))
        )


def _is_number(value: str) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True
