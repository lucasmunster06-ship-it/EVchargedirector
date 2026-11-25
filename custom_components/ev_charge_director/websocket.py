"""Websocket-API voor de kaart: live snapshot per laadpaal."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN


def _managers(hass: HomeAssistant) -> dict[str, Any]:
    return hass.data.get(DOMAIN, {}).get("entries", {})


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_list)
    websocket_api.async_register_command(hass, ws_subscribe)
    websocket_api.async_register_command(hass, ws_history)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/list"})
@callback
def ws_list(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    connection.send_result(
        msg["id"], [{"entry_id": k, "name": m.name} for k, m in _managers(hass).items()]
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/subscribe",
        vol.Optional("entry_id"): str,
    }
)
@callback
def ws_subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    managers = _managers(hass)
    entry_id = msg.get("entry_id")
    manager = managers.get(entry_id) if entry_id else next(iter(managers.values()), None)
    if manager is None:
        connection.send_error(msg["id"], "not_found", "Geen EV Charge Director-laadpaal gevonden")
        return

    @callback
    def push() -> None:
        connection.send_message(websocket_api.event_message(msg["id"], manager.snapshot()))

    connection.subscriptions[msg["id"]] = manager.async_add_listener(push)
    connection.send_result(msg["id"])
    push()


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/history",
        vol.Optional("entry_id"): str,
        vol.Optional("month"): vol.Match(r"^\d{4}-\d{2}$"),
    }
)
@websocket_api.async_response
async def ws_history(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Alle sessies (optioneel van één maand) uit de CSV's."""
    from .logic import read_history

    managers = _managers(hass)
    entry_id = msg.get("entry_id")
    manager = managers.get(entry_id) if entry_id else next(iter(managers.values()), None)
    if manager is None:
        connection.send_error(msg["id"], "not_found", "Geen laadpunt gevonden")
        return
    data = await hass.async_add_executor_job(read_history, manager.csv_read_dir, None, msg.get("month"))
    connection.send_result(msg["id"], {"dir": manager.csv_read_dir, **data})
