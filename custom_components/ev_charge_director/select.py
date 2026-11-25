"""Keuze van de auto."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import EVDirectorEntity
from .manager import EVChargeManager


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([CarSelect(entry.runtime_data)])


class CarSelect(EVDirectorEntity, SelectEntity):
    _attr_icon = "mdi:car-electric"

    def __init__(self, manager: EVChargeManager) -> None:
        super().__init__(manager, "car")
        self._attr_options = [c.name for c in manager.cars]

    @property
    def current_option(self) -> str:
        return self.manager.data["car"]

    async def async_select_option(self, option: str) -> None:
        await self.manager.async_select_car(option)
