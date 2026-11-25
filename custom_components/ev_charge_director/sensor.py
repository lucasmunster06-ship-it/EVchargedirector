"""Sensoren."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .entity import EVDirectorEntity
from .logic import previous_month
from .manager import EVChargeManager

EUR = "€"
EUR_KWH = "€/kWh"


def _ts(value: str | None):
    return dt_util.parse_datetime(value) if value else None


def _last(key: str) -> Callable[[EVChargeManager], Any]:
    return lambda m: (m.last_session or {}).get(key)


@dataclass(frozen=True, kw_only=True)
class EVSensorDescription(SensorEntityDescription):
    value_fn: Callable[[EVChargeManager], Any]
    attr_fn: Callable[[EVChargeManager], dict[str, Any]] | None = None


SENSORS: tuple[EVSensorDescription, ...] = (
    EVSensorDescription(
        key="status",
        icon="mdi:ev-station",
        value_fn=lambda m: m.status["label"],
        attr_fn=lambda m: {"raw": m.status["raw"], "category": m.status["category"]},
    ),
    EVSensorDescription(
        key="power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda m: m.power_kw,
    ),
    EVSensorDescription(
        key="price",
        icon="mdi:currency-eur",
        native_unit_of_measurement=EUR_KWH,
        suggested_display_precision=3,
        value_fn=lambda m: m.price,
    ),
    EVSensorDescription(
        key="session_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda m: round(m.data["session"]["kwh"], 3),
    ),
    EVSensorDescription(
        key="session_cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=EUR,
        suggested_display_precision=2,
        value_fn=lambda m: round(m.data["session"]["cost"], 2),
    ),
    EVSensorDescription(
        key="session_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda m: _ts(m.data["session"]["start"]),
    ),
    EVSensorDescription(
        key="last_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=_last("kwh"),
        attr_fn=lambda m: {"car": _last("car")(m), "start": _last("start")(m), "end": _last("end")(m)},
    ),
    EVSensorDescription(
        key="last_cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=EUR,
        suggested_display_precision=2,
        value_fn=_last("cost"),
    ),
    EVSensorDescription(
        key="last_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=_last("duration_min"),
    ),
    EVSensorDescription(
        key="last_avg_price",
        icon="mdi:calculator",
        native_unit_of_measurement=EUR_KWH,
        suggested_display_precision=3,
        value_fn=_last("avg_price"),
    ),
    EVSensorDescription(
        key="plan_status",
        icon="mdi:calendar-clock",
        value_fn=lambda m: m.plan.get("mode"),
        attr_fn=lambda m: {k: v for k, v in m.plan.items() if k != "slots"},
    ),
    EVSensorDescription(
        key="plan_next_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda m: _ts(m.plan.get("next_start")),
    ),
    EVSensorDescription(
        key="plan_expected_cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=EUR,
        suggested_display_precision=2,
        value_fn=lambda m: m.plan.get("expected_cost"),
    ),
    EVSensorDescription(
        key="plan_needed_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda m: m.plan.get("needed_kwh"),
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    manager: EVChargeManager = entry.runtime_data
    entities: list[SensorEntity] = [EVSensor(manager, d) for d in SENSORS]
    entities += [MonthSensor(manager, car.name, car.slug) for car in manager.cars]
    add(entities)


class EVSensor(EVDirectorEntity, SensorEntity):
    entity_description: EVSensorDescription

    def __init__(self, manager: EVChargeManager, description: EVSensorDescription) -> None:
        super().__init__(manager, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        return self.entity_description.value_fn(self.manager)

    @property
    def extra_state_attributes(self):
        fn = self.entity_description.attr_fn
        return fn(self.manager) if fn else None


class MonthSensor(EVDirectorEntity, SensorEntity):
    """Vorige maand per auto (kWh), met kosten/sessies/gemiddelde als attributen."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:calendar-month"

    def __init__(self, manager: EVChargeManager, car: str, slug: str) -> None:
        super().__init__(manager, "month")
        self._slug = slug
        self._attr_unique_id = f"{manager.entry.entry_id}_month_{slug}"
        self._attr_translation_placeholders = {"car": car}

    def _stats(self) -> tuple[str, dict[str, Any] | None]:
        month = previous_month(dt_util.now().date())
        return month, self.manager.history["months"].get(self._slug, {}).get(month)

    @property
    def native_value(self):
        _, stats = self._stats()
        return stats["kwh"] if stats else 0

    @property
    def extra_state_attributes(self):
        month, stats = self._stats()
        stats = stats or {"cost": 0, "sessions": 0, "avg_price": 0}
        return {
            "month": month,
            "cost": stats["cost"],
            "sessions": stats["sessions"],
            "avg_price": stats["avg_price"],
        }
