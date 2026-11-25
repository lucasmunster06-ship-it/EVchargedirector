"""Binaire sensoren."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CAT_CHARGING
from .entity import EVDirectorEntity
from .manager import EVChargeManager


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    m: EVChargeManager = entry.runtime_data
    add([SessionActive(m), Charging(m)])


class SessionActive(EVDirectorEntity, BinarySensorEntity):
    _attr_icon = "mdi:battery-charging"

    def __init__(self, manager: EVChargeManager) -> None:
        super().__init__(manager, "session_active")

    @property
    def is_on(self) -> bool:
        return bool(self.manager.data["session"]["active"])


class Charging(EVDirectorEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    def __init__(self, manager: EVChargeManager) -> None:
        super().__init__(manager, "charging")

    @property
    def is_on(self) -> bool:
        return self.manager.category == CAT_CHARGING
