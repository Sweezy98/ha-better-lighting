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
from typing import TYPE_CHECKING

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

from .const import DOMAIN, BindingType

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


@dataclass(slots=True)
class PressBurst:
    """A run of quick taps, collapsed into one move.

    Queueing each tap would strobe the room through every intermediate scene;
    dropping them would make the third tap do nothing. Counting them and
    resolving once does what the user meant.
    """

    count: int = 0
    kind: str = "press"
    cancel: CALLBACK_TYPE | None = None


@dataclass(slots=True)
class ControllerRuntime:
    """One configured switch, watching whatever it is bound to."""

    hass: HomeAssistant
    config: ControllerConfig
    zone: ZoneController
    contexts: ContextRegistry

    _unsubscribers: list[CALLBACK_TYPE] = field(default_factory=list)
    _burst: PressBurst = field(default_factory=PressBurst)
    _last_press: float = 0.0
    _started_at: float = 0.0

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

    # -- press detection ---------------------------------------------------

    @callback
    def _handle_state(self, event: Event[EventStateChangedData]) -> None:
        kind = self.classify(event.data.get("old_state"), event.data["new_state"])
        if kind is None:
            return
        if self.contexts.is_ours(event.context):
            return
        self.async_press(kind)

    @callback
    def _handle_report(self, event: Event[EventStateReportedData]) -> None:
        """A repeat of the value the entity already had."""
        new = event.data["new_state"]
        if new is None or self.contexts.is_ours(event.context):
            return
        # There is no "old" here by definition, so the transition guards do not
        # apply; freshness is what separates a real repeat press from noise.
        value = self._press_value(new)
        if value is None:
            return
        kind = self._kind_for(value)
        if kind is not None:
            self.async_press(kind)

    def _kind_for(self, value: str) -> str | None:
        """Which of the six things this word means, if any.

        The lower half is checked first: a rocker that publishes "off" for its
        down button would otherwise be read as an ordinary press, since "off"
        is also in the default single-press vocabulary.
        """
        config = self.config
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

    @callback
    def async_press(self, kind: str = "press") -> None:
        """Accept a press, debounce it, and coalesce a burst of them."""
        now = time.monotonic()
        interval = self.config.min_press_interval_ms / 1000
        if interval and (now - self._last_press) < interval:
            _LOGGER.debug("%s: press ignored as a bounce", self.config.name)
            return
        self._last_press = now

        self.hass.bus.async_fire(
            EVENT_PRESS,
            {
                "controller_id": self.config.subentry_id,
                "controller": self.config.name,
                "zone_id": self.config.zone_id,
                "kind": kind,
            },
        )

        window = self.config.coalesce_window_ms / 1000
        if not window or kind != "press":
            # Only plain presses accumulate. A long press means one thing and
            # should happen at once.
            self._flush_now(kind, 1)
            return

        self._burst.count += 1
        self._burst.kind = kind
        if self._burst.cancel is not None:
            self._burst.cancel()

        @callback
        def _flush(_now) -> None:
            self._burst.cancel = None
            count, self._burst.count = self._burst.count, 0
            self._flush_now(kind, count)

        self._burst.cancel = async_call_later(self.hass, window, _flush)

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
