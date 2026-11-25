"""Tijd waarop de auto klaar moet zijn."""
from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import EVDirectorEntity
from .manager import EVChargeManager


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([ReadyTime(entry.runtime_data)])


class ReadyTime(EVDirectorEntity, TimeEntity):
    _attr_icon = "mdi:clock-end"

    def __init__(self, manager: EVChargeManager) -> None:
        super().__init__(manager, "ready_time")

    @property
    def native_value(self) -> time:
        hh, mm = (int(x) for x in str(self.manager.data["smart"]["ready_time"]).split(":")[:2])
        return time(hh, mm)

    async def async_set_value(self, value: time) -> None:
        await self.manager.async_set_smart(ready_time=value)
