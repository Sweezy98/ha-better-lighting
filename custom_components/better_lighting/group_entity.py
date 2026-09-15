"""A minimal group-entity base, vendored from Home Assistant core.

Adapted from ``homeassistant/components/group/entity.py`` as of HA 2026.9.2.

We vendor rather than subclass ``homeassistant.components.group.entity.GroupEntity``
because that class is not public API for custom integrations -- it lives inside
the ``group`` integration, its constructor has changed shape across releases,
and importing it would force a ``group`` dependency and a setup-order coupling
for roughly 120 lines we need to modify anyway.  Relative Light Group made the
same call and has tracked core across several releases without breaking.

Two deliberate differences from core:

1. :meth:`async_update_group_state` returns ``bool``.  Returning ``False`` means
   "I did not recompute, do not write state", which lets a subclass swallow the
   echo of its own commands without the group flickering through a stale value.
2. :meth:`async_should_defer_state_change` is a new hook, consulted for every
   member state change, so a subclass can ignore updates caused by its own
   in-flight service calls.
"""

from __future__ import annotations

from abc import abstractmethod

from homeassistant.const import ATTR_ASSUMED_STATE, ATTR_ENTITY_ID
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers import start
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_state_change_event


class GroupEntity(Entity):
    """An entity whose state is derived from a fixed set of member entities."""

    _unrecorded_attributes = frozenset({ATTR_ENTITY_ID})

    _attr_should_poll = False
    _entity_ids: list[str]

    async def async_added_to_hass(self) -> None:
        """Start tracking the members."""

        @callback
        def async_state_changed_listener(event: Event[EventStateChangedData]) -> None:
            if self.async_should_defer_state_change(event):
                return
            # Propagate the originating context so the logbook attributes a
            # member change to whatever actually caused it.
            self.async_set_context(event.context)
            self.async_defer_or_update_ha_state()

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._entity_ids, async_state_changed_listener
            )
        )
        self.async_on_remove(start.async_at_start(self.hass, self._update_at_start))

    @callback
    def _update_at_start(self, _: HomeAssistant) -> None:
        """Compute the initial state once HA has finished starting."""
        if self.async_update_group_state():
            self.async_write_ha_state()

    @callback
    def async_defer_or_update_ha_state(self) -> None:
        """Recompute and write, unless HA is still starting.

        During startup member states arrive in bursts and many are still
        ``unavailable``; ``_update_at_start`` does one computation at the end
        instead, so the group never publishes a half-built state.
        """
        if not self.hass.is_running:
            return
        if self.async_update_group_state():
            self.async_write_ha_state()

    @abstractmethod
    @callback
    def async_update_group_state(self) -> bool:
        """Recompute this entity's state from its members.

        Return ``True`` if the state was recomputed and should be written,
        ``False`` to leave the currently published state alone.
        """

    @callback
    def async_should_defer_state_change(
        self, event: Event[EventStateChangedData]
    ) -> bool:
        """Return True to ignore this member update entirely."""
        return False

    @callback
    def _update_assumed_state_from_members(self) -> None:
        """A group is assumed-state if any member is."""
        self._attr_assumed_state = any(
            state.attributes.get(ATTR_ASSUMED_STATE)
            for entity_id in self._entity_ids
            if (state := self.hass.states.get(entity_id)) is not None
        )
