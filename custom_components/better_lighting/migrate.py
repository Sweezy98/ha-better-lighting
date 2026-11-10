"""Rewriting stored settings whose meaning has moved.

Each step is a pure function of what was stored, so it can be read, and
tested, without a running Home Assistant.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_NIGHT_BEHAVIOR,
    CONF_NIGHT_BRIGHTNESS_PCT,
    CONF_NIGHT_COLOR_TEMP_K,
    CONF_NIGHT_LOOK,
    HUB_SPECS,
    ROOM_NIGHT_SPECS,
    NightBehavior,
    NightLook,
    SubentryType,
    defaults_for,
)

CURRENT_MINOR_VERSION = 2

_NIGHT_VALUES = (CONF_NIGHT_BRIGHTNESS_PCT, CONF_NIGHT_COLOR_TEMP_K)


def room_night_look(room: dict[str, Any], hub: dict[str, Any]) -> dict[str, Any] | None:
    """Give a room stored before night had a look of its own one.

    Its night values used to be the room's own, always; now a room follows
    the global ones unless it says otherwise. So it says otherwise exactly
    when its values were not the global ones anyway -- and "apply a night
    scene" becomes dimming, with that scene for its look. Returns None when
    there is nothing to change.
    """
    if CONF_NIGHT_LOOK in room:
        return None
    room_defaults = defaults_for(ROOM_NIGHT_SPECS)
    hub_values = {**defaults_for(HUB_SPECS), **hub}
    behavior = room.get(CONF_NIGHT_BEHAVIOR, NightBehavior.MIN_SETTINGS.value)
    if behavior == NightBehavior.SCENE.value:
        behavior, look = NightBehavior.MIN_SETTINGS.value, NightLook.SCENE
    elif any(
        float(room.get(key, room_defaults[key])) != float(hub_values[key])
        for key in _NIGHT_VALUES
    ):
        look = NightLook.CUSTOM
    else:
        look = NightLook.GLOBAL
    # Stored without a behaviour meant the default of the time, which was to
    # dim; the default now is to darken an empty room, and a room nobody has
    # touched should not start doing that by itself.
    return {**room, CONF_NIGHT_BEHAVIOR: behavior, CONF_NIGHT_LOOK: look.value}


async def async_migrate(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Bring a stored entry up to the current minor version."""
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        for subentry in list(entry.subentries.values()):
            if subentry.subentry_type != SubentryType.ROOM.value:
                continue
            updated = room_night_look(dict(subentry.data), dict(entry.options))
            if updated is not None:
                hass.config_entries.async_update_subentry(entry, subentry, data=updated)
    hass.config_entries.async_update_entry(entry, minor_version=CURRENT_MINOR_VERSION)
    return True
