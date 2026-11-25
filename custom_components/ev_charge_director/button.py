"""Knoppen."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import EVDirectorEntity
from .manager import EVChargeManager


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    m: EVChargeManager = entry.runtime_data
    add([
        ActionButton(m, "start", "mdi:play-circle-outline", m.async_start),
        ActionButton(m, "stop", "mdi:stop-circle-outline", m.async_stop),
        ActionButton(m, "next_car", "mdi:car-cog", m.async_next_car),
        ActionButton(m, "resume_plan", "mdi:calendar-sync", m.async_clear_override),
        ActionButton(m, "monthly_report", "mdi:file-chart", m.async_monthly_report),
    ])


class ActionButton(EVDirectorEntity, ButtonEntity):
    def __init__(self, manager: EVChargeManager, key: str, icon: str, action) -> None:
        super().__init__(manager, key)
        self._attr_icon = icon
        self._action = action

    async def async_press(self) -> None:
        await self._action()
