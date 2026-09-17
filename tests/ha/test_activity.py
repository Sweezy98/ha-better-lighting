"""The activity log, which has to survive a restart to be worth anything."""

from __future__ import annotations

import datetime as dt

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.better_lighting.activity import ActivityLog


class _Event:
    def __init__(self, event_type: str, data: dict) -> None:
        self.event_type = event_type
        self.data = data


class TestRetention:
    async def test_it_keeps_what_happened(self, hass: HomeAssistant) -> None:
        log = ActivityLog(hass, 48)
        await log.async_load()

        log.async_record(_Event("better_lighting_press", {"kind": "press"}))

        assert [entry["kind"] for entry in log.recent()] == ["press"]

    async def test_zero_hours_keeps_nothing(self, hass: HomeAssistant) -> None:
        """Off has to mean off: the page then shows only what happens while
        it is open, which is what it did before any of this."""
        log = ActivityLog(hass, 0)
        await log.async_load()

        log.async_record(_Event("better_lighting_press", {}))

        assert log.recent() == []

    async def test_what_has_aged_out_goes(self, hass: HomeAssistant) -> None:
        log = ActivityLog(hass, 48)
        await log.async_load()
        old = (dt_util.utcnow() - dt.timedelta(hours=72)).isoformat()
        log._entries = [{"at": old, "kind": "press", "data": {}}]

        log.async_record(_Event("better_lighting_press", {"kind": "now"}))

        assert [entry["data"] for entry in log.recent()] == [{"kind": "now"}]

    async def test_clearing_empties_it(self, hass: HomeAssistant) -> None:
        log = ActivityLog(hass, 48)
        await log.async_load()
        log.async_record(_Event("better_lighting_press", {}))

        log.async_clear()

        assert log.recent() == []
