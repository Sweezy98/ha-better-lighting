"""Running somebody else's scripts, without letting them stop ours.

Lighting is not everything a scene or a mode means: the amplifier goes on
with the film and off again when the room comes back, the blinds come down,
something gets announced. Both of those places need the same three
decisions, so they are made once, here.

Fired rather than awaited, because a script that dims over thirty seconds or
waits for a door must not hold up the room next to it. In no context of ours,
because a script is somebody's own instruction and whatever it turns on
should be read as exactly that rather than mistaken for our own command
coming back. And a script that has since been deleted is a warning naming it,
not an unhandled task exception every time the scene is used.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceNotFound

_LOGGER = logging.getLogger(__name__)


def async_run_scripts(hass: HomeAssistant, scripts: Iterable[str], owner: str) -> None:
    """Start ``scripts``. ``owner`` is who to name if one of them is gone."""
    wanted = [script for script in scripts if script]
    if not wanted:
        return
    _LOGGER.debug("%s: running %s", owner, ", ".join(wanted))

    async def _run() -> None:
        try:
            await hass.services.async_call(
                "script", "turn_on", {"entity_id": wanted}, blocking=False
            )
        except (ServiceNotFound, vol.Invalid) as err:
            _LOGGER.warning("%s could not run %s: %s", owner, ", ".join(wanted), err)

    hass.async_create_task(_run())
