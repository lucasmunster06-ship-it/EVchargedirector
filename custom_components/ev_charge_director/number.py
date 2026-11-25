"""Laaddoel in kWh of %."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import EVDirectorEntity
from .manager import EVChargeManager


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    m: EVChargeManager = entry.runtime_data
    add([TargetEnergy(m), TargetSoc(m)])


class TargetEnergy(EVDirectorEntity, NumberEntity):
    _attr_icon = "mdi:lightning-bolt"
    _attr_native_min_value = 0
    _attr_native_max_value = 150
    _attr_native_step = 0.5
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, manager: EVChargeManager) -> None:
        super().__init__(manager, "target_energy")

    @property
    def native_value(self) -> float:
        return float(self.manager.data["smart"]["target_kwh"])

    async def async_set_native_value(self, value: float) -> None:
        await self.manager.async_set_smart(target_kwh=value)


class TargetSoc(EVDirectorEntity, NumberEntity):
    _attr_icon = "mdi:battery-high"
    _attr_native_min_value = 10
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, manager: EVChargeManager) -> None:
        super().__init__(manager, "target_soc")

    @property
    def native_value(self) -> float:
        return float(self.manager.data["smart"]["target_soc"])

    async def async_set_native_value(self, value: float) -> None:
        await self.manager.async_set_smart(target_soc=value)
