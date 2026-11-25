"""Config flow: alles is zelf te koppelen, niets is vast aan één merk laadpaal."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector as sel

from .const import (
    CATEGORIES,
    CONF_CAN_START,
    CONF_CAN_STOP,
    CONF_CARS,
    CONF_CSV_DIR,
    CONF_CSV_ENABLED,
    CONF_CSV_READ_DIR,
    CONF_END_CATEGORIES,
    CONF_ENERGY,
    CONF_FORECAST,
    CONF_MAX_POWER,
    CONF_NAME,
    CONF_POWER,
    CONF_PRICE,
    CONF_REPORT_DAY,
    CONF_REPORT_HOUR,
    CONF_SAFETY_MARGIN,
    CONF_START,
    CONF_STATUS,
    CONF_STATUS_MAP,
    CONF_STOP,
    DEFAULTS,
    DOMAIN,
)
from .logic import validate_cars, validate_status_map

CONTROL_DOMAINS = ["button", "input_button", "switch", "input_boolean", "script"]

SOURCE_KEYS = [CONF_STATUS, CONF_ENERGY, CONF_POWER, CONF_PRICE, CONF_FORECAST]
CONTROL_KEYS = [CONF_START, CONF_STOP, CONF_CAN_START, CONF_CAN_STOP]
STATUS_KEYS = [CONF_STATUS_MAP, CONF_END_CATEGORIES]
CAR_KEYS = [CONF_CARS]
SMART_KEYS = [CONF_MAX_POWER, CONF_SAFETY_MARGIN]
STORAGE_KEYS = [CONF_CSV_ENABLED, CONF_CSV_DIR, CONF_CSV_READ_DIR, CONF_REPORT_DAY, CONF_REPORT_HOUR]


def _ent(domain: str | list[str] | None = None) -> sel.EntitySelector:
    cfg = sel.EntitySelectorConfig(domain=domain) if domain else sel.EntitySelectorConfig()
    return sel.EntitySelector(cfg)


def _opt(key: str, cur: dict[str, Any]) -> vol.Optional:
    """Optioneel veld dat ook weer leeg gemaakt kan worden."""
    val = cur.get(key)
    return vol.Optional(key, description={"suggested_value": val} if val not in (None, "") else None)


def _sources_schema(cur: dict[str, Any], with_name: bool) -> vol.Schema:
    fields: dict[Any, Any] = {}
    if with_name:
        fields[vol.Required(CONF_NAME, default=cur.get(CONF_NAME, "Laadpaal"))] = sel.TextSelector()
    fields[vol.Required(CONF_STATUS, default=cur.get(CONF_STATUS, vol.UNDEFINED))] = _ent()
    fields[_opt(CONF_ENERGY, cur)] = _ent("sensor")
    fields[_opt(CONF_POWER, cur)] = _ent("sensor")
    fields[_opt(CONF_PRICE, cur)] = _ent(["sensor", "input_number"])
    fields[_opt(CONF_FORECAST, cur)] = _ent()
    return vol.Schema(fields)


def _control_schema(cur: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            _opt(CONF_START, cur): _ent(CONTROL_DOMAINS),
            _opt(CONF_STOP, cur): _ent(CONTROL_DOMAINS),
            _opt(CONF_CAN_START, cur): _ent(["binary_sensor", "input_boolean", "switch"]),
            _opt(CONF_CAN_STOP, cur): _ent(["binary_sensor", "input_boolean", "switch"]),
        }
    )


def _status_schema(cur: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_STATUS_MAP, default=cur.get(CONF_STATUS_MAP, DEFAULTS[CONF_STATUS_MAP])): sel.TextSelector(
                sel.TextSelectorConfig(multiline=True)
            ),
            vol.Required(
                CONF_END_CATEGORIES, default=cur.get(CONF_END_CATEGORIES, DEFAULTS[CONF_END_CATEGORIES])
            ): sel.SelectSelector(
                sel.SelectSelectorConfig(
                    options=CATEGORIES,
                    multiple=True,
                    translation_key="category",
                    mode=sel.SelectSelectorMode.LIST,
                )
            ),
        }
    )


def _cars_schema(cur: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_CARS, default=cur.get(CONF_CARS, DEFAULTS[CONF_CARS])): sel.TextSelector(
                sel.TextSelectorConfig(multiline=True)
            )
        }
    )


def _smart_schema(cur: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_MAX_POWER, default=cur.get(CONF_MAX_POWER, DEFAULTS[CONF_MAX_POWER])): sel.NumberSelector(
                sel.NumberSelectorConfig(min=1, max=50, step=0.1, unit_of_measurement="kW", mode=sel.NumberSelectorMode.BOX)
            ),
            vol.Required(
                CONF_SAFETY_MARGIN, default=cur.get(CONF_SAFETY_MARGIN, DEFAULTS[CONF_SAFETY_MARGIN])
            ): sel.NumberSelector(
                sel.NumberSelectorConfig(min=0, max=180, step=5, unit_of_measurement="min", mode=sel.NumberSelectorMode.BOX)
            ),
        }
    )


def _storage_schema(cur: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_CSV_ENABLED, default=cur.get(CONF_CSV_ENABLED, DEFAULTS[CONF_CSV_ENABLED])): sel.BooleanSelector(),
            vol.Required(CONF_CSV_DIR, default=cur.get(CONF_CSV_DIR, DEFAULTS[CONF_CSV_DIR])): sel.TextSelector(),
            _opt(CONF_CSV_READ_DIR, cur): sel.TextSelector(),
            vol.Required(CONF_REPORT_DAY, default=cur.get(CONF_REPORT_DAY, DEFAULTS[CONF_REPORT_DAY])): sel.NumberSelector(
                sel.NumberSelectorConfig(min=1, max=28, step=1, mode=sel.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_REPORT_HOUR, default=cur.get(CONF_REPORT_HOUR, DEFAULTS[CONF_REPORT_HOUR])): sel.NumberSelector(
                sel.NumberSelectorConfig(min=0, max=23, step=1, mode=sel.NumberSelectorMode.BOX)
            ),
        }
    )


def _clean(user_input: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    """Velden die leeg gelaten zijn expliciet op None zetten."""
    out = {k: user_input.get(k) for k in keys}
    for k in (CONF_REPORT_DAY, CONF_REPORT_HOUR, CONF_SAFETY_MARGIN):
        if out.get(k) is not None:
            out[k] = int(out[k])
    return out


def _validate(step_keys: list[str], user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if CONF_STATUS_MAP in step_keys:
        if err := validate_status_map(user_input.get(CONF_STATUS_MAP, "")):
            errors[CONF_STATUS_MAP] = err
    if CONF_CARS in step_keys:
        if err := validate_cars(user_input.get(CONF_CARS, "")):
            errors[CONF_CARS] = err
    return errors


class EVChargeDirectorConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return EVChargeDirectorOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_STATUS])
            self._abort_if_unique_id_configured()
            self._data.update({CONF_NAME: user_input[CONF_NAME]}, **_clean(user_input, SOURCE_KEYS))
            return await self.async_step_control()
        return self.async_show_form(step_id="user", data_schema=_sources_schema({}, True))

    async def async_step_control(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(_clean(user_input, CONTROL_KEYS))
            return await self.async_step_status()
        return self.async_show_form(step_id="control", data_schema=_control_schema({}))

    async def async_step_status(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            errors = _validate(STATUS_KEYS, user_input)
            if not errors:
                self._data.update(_clean(user_input, STATUS_KEYS))
                return await self.async_step_cars()
        return self.async_show_form(
            step_id="status", data_schema=_status_schema(user_input or {}), errors=errors
        )

    async def async_step_cars(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            errors = _validate(CAR_KEYS, user_input)
            if not errors:
                self._data.update(_clean(user_input, CAR_KEYS))
                return self.async_create_entry(title=self._data[CONF_NAME], data=self._data)
        return self.async_show_form(step_id="cars", data_schema=_cars_schema(user_input or {}), errors=errors)


class EVChargeDirectorOptionsFlow(OptionsFlow):
    """Menu om elk onderdeel later aan te passen."""

    @property
    def _cur(self) -> dict[str, Any]:
        return {**DEFAULTS, **self.config_entry.data, **self.config_entry.options}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init", menu_options=["sources", "control", "status", "cars", "smart", "storage"]
        )

    async def _step(self, step_id: str, keys: list[str], schema: vol.Schema, user_input) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            errors = _validate(keys, user_input)
            if not errors:
                return self.async_create_entry(data={**self.config_entry.options, **_clean(user_input, keys)})
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)

    async def async_step_sources(self, user_input=None) -> ConfigFlowResult:
        return await self._step("sources", SOURCE_KEYS, _sources_schema(self._cur, False), user_input)

    async def async_step_control(self, user_input=None) -> ConfigFlowResult:
        return await self._step("control", CONTROL_KEYS, _control_schema(self._cur), user_input)

    async def async_step_status(self, user_input=None) -> ConfigFlowResult:
        return await self._step("status", STATUS_KEYS, _status_schema(self._cur), user_input)

    async def async_step_cars(self, user_input=None) -> ConfigFlowResult:
        return await self._step("cars", CAR_KEYS, _cars_schema(self._cur), user_input)

    async def async_step_smart(self, user_input=None) -> ConfigFlowResult:
        return await self._step("smart", SMART_KEYS, _smart_schema(self._cur), user_input)

    async def async_step_storage(self, user_input=None) -> ConfigFlowResult:
        return await self._step("storage", STORAGE_KEYS, _storage_schema(self._cur), user_input)
