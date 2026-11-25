"""EV Charge Director: laadsessies, kosten en slim laden voor elke laadpaal."""
from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CARD_FILENAME,
    CARD_URL,
    DOMAIN,
    PLATFORMS,
    SERVICE_CLEAR_OVERRIDE,
    SERVICE_MONTHLY_REPORT,
    SERVICE_NEXT_CAR,
    SERVICE_SELECT_CAR,
    SERVICE_SET_SMART,
    SERVICE_START,
    SERVICE_STOP,
)
from .manager import EVChargeManager
from .websocket import async_register_websocket

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

ENTRY = vol.Optional("config_entry_id")
BASE_SCHEMA = vol.Schema({ENTRY: cv.string})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data.setdefault(DOMAIN, {})
    await _async_register_card(hass)
    async_register_websocket(hass)
    _register_services(hass)
    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serveer de kaart vanuit de integratie en laad hem op elke pagina."""
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    path = Path(__file__).parent / "www" / CARD_FILENAME
    version = "0"
    try:
        manifest = Path(__file__).parent / "manifest.json"
        import json

        version = json.loads(await hass.async_add_executor_job(manifest.read_text))["version"]
    except Exception:  # noqa: BLE001
        pass
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(path), False)])
    add_extra_js_url(hass, f"{CARD_URL}?v={version}")


def _manager(hass: HomeAssistant, call: ServiceCall) -> EVChargeManager:
    managers: dict[str, EVChargeManager] = hass.data.get(DOMAIN, {}).get("entries", {})
    entry_id = call.data.get("config_entry_id")
    if entry_id:
        if entry_id not in managers:
            raise ServiceValidationError(f"Onbekende laadpaal: {entry_id}")
        return managers[entry_id]
    if not managers:
        raise ServiceValidationError("EV Charge Director is niet ingesteld")
    return next(iter(managers.values()))


def _register_services(hass: HomeAssistant) -> None:
    async def start(call: ServiceCall) -> None:
        await _manager(hass, call).async_start()

    async def stop(call: ServiceCall) -> None:
        await _manager(hass, call).async_stop()

    async def clear_override(call: ServiceCall) -> None:
        await _manager(hass, call).async_clear_override()

    async def next_car(call: ServiceCall) -> None:
        await _manager(hass, call).async_next_car()

    async def select_car(call: ServiceCall) -> None:
        try:
            await _manager(hass, call).async_select_car(call.data["car"])
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

    async def set_smart(call: ServiceCall) -> None:
        await _manager(hass, call).async_set_smart(
            enabled=call.data.get("enabled"),
            ready_time=call.data.get("ready_time"),
            target_kwh=call.data.get("target_kwh"),
            target_soc=call.data.get("target_soc"),
        )

    async def report(call: ServiceCall) -> None:
        await _manager(hass, call).async_monthly_report(call.data.get("car"), call.data.get("month"))

    hass.services.async_register(DOMAIN, SERVICE_START, start, BASE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_STOP, stop, BASE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_OVERRIDE, clear_override, BASE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_NEXT_CAR, next_car, BASE_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_SELECT_CAR, select_car, BASE_SCHEMA.extend({vol.Required("car"): cv.string})
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SMART,
        set_smart,
        BASE_SCHEMA.extend(
            {
                vol.Optional("enabled"): cv.boolean,
                vol.Optional("ready_time"): cv.time,
                vol.Optional("target_kwh"): vol.All(vol.Coerce(float), vol.Range(min=0, max=200)),
                vol.Optional("target_soc"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_MONTHLY_REPORT,
        report,
        BASE_SCHEMA.extend(
            {
                vol.Optional("car"): cv.string,
                vol.Optional("month"): vol.Match(r"^\d{4}-\d{2}$"),
            }
        ),
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    manager = EVChargeManager(hass, entry)
    await manager.async_setup()
    hass.data[DOMAIN].setdefault("entries", {})[entry.entry_id] = manager
    entry.runtime_data = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        manager: EVChargeManager = hass.data[DOMAIN]["entries"].pop(entry.entry_id)
        await manager.async_unload()
    return ok
