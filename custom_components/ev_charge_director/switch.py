"""Schakelaar voor slim laden."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import EVDirectorEntity
from .manager import EVChargeManager


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([SmartCharging(entry.runtime_data)])


class SmartCharging(EVDirectorEntity, SwitchEntity):
    _attr_icon = "mdi:clock-time-four-outline"

    def __init__(self, manager: EVChargeManager) -> None:
        super().__init__(manager, "smart_charging")

    @property
    def is_on(self) -> bool:
        return bool(self.manager.data["smart"]["enabled"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"override": self.manager.data["smart"]["override"]}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.manager.async_set_smart(enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.manager.async_set_smart(enabled=False)
