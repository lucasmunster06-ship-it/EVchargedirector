"""Centrale logica: sessies bijhouden, slim laden, CSV-log en maandrapport."""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CAT_CHARGING,
    CAT_CONNECTED,
    CAT_DISCONNECTED,
    CAT_ERROR,
    CAT_FINISHED,
    CAT_PAUSED,
    CAT_UNKNOWN,
    CONF_CAN_START,
    CONF_CAN_STOP,
    CONF_CARS,
    CONF_CSV_DIR,
    CONF_CSV_ENABLED,
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
    STORAGE_VERSION,
)
from .logic import (
    Car,
    Slot,
    append_session_csv,
    extract_slots,
    format_duration,
    next_occurrence,
    normalize_raw_status,
    parse_cars,
    parse_status_map,
    plan_cheapest,
    previous_month,
    read_history,
)

_LOGGER = logging.getLogger(__name__)

BAD_STATES = (STATE_UNAVAILABLE, STATE_UNKNOWN, "none", "", None)
COMMAND_COOLDOWN = timedelta(seconds=90)
MAX_DELTA_KWH = 100.0


def _default_data(cars: list[Car]) -> dict[str, Any]:
    return {
        "car": cars[0].name,
        "session": {
            "active": False,
            "start": None,
            "kwh": 0.0,
            "cost": 0.0,
            "prev_meter": None,
            "last_power_kw": None,
            "last_power_ts": None,
        },
        "last_session": None,
        "smart": {
            "enabled": False,
            "ready_time": "07:00",
            "target_kwh": 20.0,
            "target_soc": 80,
            "override": None,  # None | "on" | "off"
            "deadline": None,
            "charged_kwh": 0.0,
            "done": False,
        },
    }


class EVChargeManager:
    """Eén laadpunt."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.conf: dict[str, Any] = {**DEFAULTS, **entry.data, **entry.options}
        self.name: str = self.conf.get(CONF_NAME) or entry.title
        self.status_map = parse_status_map(self.conf[CONF_STATUS_MAP])
        self.cars = parse_cars(self.conf[CONF_CARS])
        self.end_categories = set(self.conf.get(CONF_END_CATEGORIES) or [CAT_DISCONNECTED])
        self.max_power = float(self.conf.get(CONF_MAX_POWER) or 11.0)
        self.store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.data: dict[str, Any] = _default_data(self.cars)
        self.history: dict[str, Any] = {"months": {}, "sessions": []}
        self.plan: dict[str, Any] = {"mode": "off"}
        self._slots: list[Slot] = []
        self._listeners: set[Callable[[], None]] = set()
        self._unsubs: list[Callable[[], None]] = []
        self._last_command: datetime | None = None
        self._last_command_kind: str | None = None
        self._attempts = 0  # mislukte start/stop-pogingen sinds laatste statuswissel

    # ------------------------------------------------------------------ setup
    async def async_setup(self) -> None:
        stored = await self.store.async_load()
        if isinstance(stored, dict):
            for key in ("session", "smart"):
                if isinstance(stored.get(key), dict):
                    self.data[key].update(stored[key])
            if stored.get("last_session"):
                self.data["last_session"] = stored["last_session"]
            if stored.get("car"):
                self.data["car"] = stored["car"]
        if self.data["car"] not in [c.name for c in self.cars]:
            self.data["car"] = self.cars[0].name

        watch = [
            self.conf.get(k)
            for k in (CONF_STATUS, CONF_ENERGY, CONF_POWER, CONF_PRICE, CONF_FORECAST, CONF_CAN_START, CONF_CAN_STOP)
        ] + [c.soc_entity for c in self.cars]
        watch = sorted({e for e in watch if e})
        self._unsubs.append(async_track_state_change_event(self.hass, watch, self._on_state_event))
        self._unsubs.append(async_track_time_interval(self.hass, self._on_tick, timedelta(seconds=30)))
        self._unsubs.append(
            async_track_time_change(
                self.hass, self._on_report_time, hour=int(self.conf[CONF_REPORT_HOUR]), minute=0, second=5
            )
        )
        self._unsubs.append(async_track_time_change(self.hass, self._on_hourly, minute=1, second=0))

        self._refresh_slots()
        await self.async_refresh_history()
        # Status kan tijdens een herstart veranderd zijn
        await self._handle_status(None, self.status)
        await self._evaluate()

    async def async_unload(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self.store.async_save(self.data)

    def _save(self) -> None:
        self.store.async_delay_save(lambda: self.data, 5)

    @callback
    def async_add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.add(cb)

        def remove() -> None:
            self._listeners.discard(cb)

        return remove

    @callback
    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Listener failed")

    # ---------------------------------------------------------------- helpers
    def _state(self, key: str) -> State | None:
        eid = self.conf.get(key)
        return self.hass.states.get(eid) if eid else None

    @staticmethod
    def _value(state: State | None) -> float | None:
        if state is None or state.state in BAD_STATES:
            return None
        try:
            return float(str(state.state).replace(",", "."))
        except ValueError:
            return None

    def _scaled(self, state: State | None, kind: str) -> float | None:
        """Zet W->kW, Wh->kWh, ct->€ op basis van de eenheid van de bron."""
        v = self._value(state)
        if v is None or state is None:
            return None
        unit = str(state.attributes.get("unit_of_measurement") or "").lower()
        if kind == "power":
            if unit == "w":
                return v / 1000
            if unit == "mw":
                return v * 1000
        elif kind == "energy":
            if unit == "wh":
                return v / 1000
            if unit == "mwh":
                return v * 1000
        elif kind == "price":
            if "ct" in unit or "cent" in unit:
                return v / 100
            if "mwh" in unit:
                return v / 1000
        return v

    @property
    def status(self) -> dict[str, Any]:
        st = self._state(CONF_STATUS)
        if st is None or st.state in BAD_STATES:
            return {"raw": st.state if st else None, "category": CAT_UNKNOWN, "label": "Status onbekend"}
        key = normalize_raw_status(st.state)
        entry = self.status_map.get(key) or self.status_map.get(str(st.state).strip().lower())
        if entry is None:
            return {"raw": st.state, "category": CAT_UNKNOWN, "label": str(st.state)}
        return {"raw": st.state, "category": entry.category, "label": entry.label}

    @property
    def category(self) -> str:
        return self.status["category"]

    @property
    def power_kw(self) -> float | None:
        p = self._scaled(self._state(CONF_POWER), "power")
        return round(p, 3) if p is not None else None

    @property
    def price(self) -> float | None:
        p = self._scaled(self._state(CONF_PRICE), "price")
        if p is None and self._slots:
            now = dt_util.now()
            for s in self._slots:
                if s.start <= now < s.end:
                    return s.price
        return p

    @property
    def meter_kwh(self) -> float | None:
        return self._scaled(self._state(CONF_ENERGY), "energy")

    def _flag(self, key: str) -> bool:
        """can_start / can_stop. Niet gekoppeld = altijd toegestaan."""
        if not self.conf.get(key):
            return True
        st = self._state(key)
        return st is not None and st.state == "on"

    @property
    def can_start(self) -> bool:
        return bool(self.conf.get(CONF_START)) and self._flag(CONF_CAN_START)

    @property
    def can_stop(self) -> bool:
        return bool(self.conf.get(CONF_STOP) or self.conf.get(CONF_START)) and self._flag(CONF_CAN_STOP)

    @property
    def car(self) -> Car:
        for c in self.cars:
            if c.name == self.data["car"]:
                return c
        return self.cars[0]

    def car_soc(self, car: Car) -> float | None:
        if not car.soc_entity:
            return None
        return self._value(self.hass.states.get(car.soc_entity))

    @property
    def csv_dir(self) -> str:
        return self.hass.config.path(str(self.conf.get(CONF_CSV_DIR) or "laadpaal"))

    # ----------------------------------------------------------------- events
    async def _on_state_event(self, event: Event) -> None:
        eid = event.data.get("entity_id")
        old: State | None = event.data.get("old_state")
        new: State | None = event.data.get("new_state")
        if eid == self.conf.get(CONF_FORECAST) or (
            eid == self.conf.get(CONF_PRICE) and not self.conf.get(CONF_FORECAST)
        ):
            self._refresh_slots()
        if eid == self.conf.get(CONF_STATUS):
            self._attempts = 0
            old_cat = self._category_of(old)
            await self._handle_status(old_cat, self.status)
        elif eid == self.conf.get(CONF_ENERGY):
            self._handle_meter(new)
        elif eid == self.conf.get(CONF_POWER):
            self._integrate_power()
        await self._evaluate()

    def _category_of(self, state: State | None) -> str | None:
        if state is None or state.state in BAD_STATES:
            return None
        e = self.status_map.get(normalize_raw_status(state.state))
        return e.category if e else CAT_UNKNOWN

    async def _on_tick(self, _now: datetime) -> None:
        self._integrate_power()
        await self._evaluate()

    async def _on_hourly(self, _now: datetime) -> None:
        self._refresh_slots()

    async def _handle_status(self, old_cat: str | None, status: dict[str, Any]) -> None:
        cat = status["category"]
        sess = self.data["session"]
        if cat == CAT_CHARGING and not sess["active"]:
            self._start_session()
        elif sess["active"] and cat in self.end_categories:
            # ook na een herstart als de auto tussendoor is losgekoppeld
            await self._finish_session()
        if cat == CAT_DISCONNECTED:
            self._reset_plan()
        self._notify()

    # --------------------------------------------------------------- sessies
    def _start_session(self) -> None:
        now = dt_util.now()
        s = self.data["session"]
        s.update(
            active=True,
            start=now.isoformat(),
            kwh=0.0,
            cost=0.0,
            prev_meter=self.meter_kwh,
            last_power_kw=self.power_kw,
            last_power_ts=now.isoformat(),
        )
        _LOGGER.debug("Laadsessie gestart voor %s", self.data["car"])
        self._save()

    def _add_energy(self, delta: float) -> None:
        if delta <= 0 or delta > MAX_DELTA_KWH:
            return
        s = self.data["session"]
        price = self.price
        s["kwh"] = round(s["kwh"] + delta, 4)
        if price is not None:
            s["cost"] = round(s["cost"] + delta * price, 4)
        if self.data["smart"]["deadline"]:
            self.data["smart"]["charged_kwh"] = round(self.data["smart"]["charged_kwh"] + delta, 4)
        self._save()

    def _handle_meter(self, new: State | None) -> None:
        s = self.data["session"]
        if not s["active"] or new is None or new.state in BAD_STATES:
            return
        meter = self._scaled(new, "energy")
        if meter is None:
            return
        prev = s.get("prev_meter")
        if prev is not None and meter > prev and self.category != CAT_DISCONNECTED:
            self._add_energy(meter - prev)
        # gelijk of gedaald (dagteller-reset): alleen de referentie bijwerken
        s["prev_meter"] = meter
        self._save()

    def _integrate_power(self) -> None:
        """Alleen als er geen energiemeter gekoppeld is: vermogen x tijd."""
        if self.conf.get(CONF_ENERGY):
            return
        s = self.data["session"]
        now = dt_util.now()
        if s["active"] and s.get("last_power_ts") and s.get("last_power_kw") is not None:
            last = dt_util.parse_datetime(s["last_power_ts"])
            if last is not None:
                hours = (now - last).total_seconds() / 3600
                if 0 < hours < 0.25:
                    self._add_energy(float(s["last_power_kw"]) * hours)
        s["last_power_kw"] = self.power_kw if self.category == CAT_CHARGING else 0.0
        s["last_power_ts"] = now.isoformat()

    async def _finish_session(self) -> None:
        s = self.data["session"]
        now = dt_util.now()
        start = dt_util.parse_datetime(s["start"]) if s.get("start") else None
        start = start or now
        kwh = round(float(s["kwh"]), 3)
        cost = round(float(s["cost"]), 2)
        avg = round(cost / kwh, 3) if kwh > 0 else 0.0
        duration = int(round((now - start).total_seconds() / 60))
        car = self.car
        self.data["last_session"] = {
            "car": car.name,
            "start": start.isoformat(),
            "end": now.isoformat(),
            "kwh": kwh,
            "cost": cost,
            "avg_price": avg,
            "duration_min": duration,
        }
        s.update(active=False, start=None, kwh=0.0, cost=0.0, prev_meter=None)
        self._reset_plan()
        self._save()
        self._notify()

        if kwh > 0 and self.conf.get(CONF_CSV_ENABLED, True):
            try:
                await self.hass.async_add_executor_job(
                    append_session_csv, self.csv_dir, car.name, start, now, kwh, cost, avg, duration
                )
            except OSError:
                _LOGGER.exception("Kon laadsessie niet naar CSV schrijven")
            await self.async_refresh_history()

        if car.notify and kwh > 0:
            msg = (
                f"Er is {kwh} kWh geladen in {format_duration(duration)} en dit kostte "
                f"€{cost:.2f} met een gemiddelde prijs van €{avg:.3f}/kWh."
            )
            await self._send(car.notify, f"Laadsessie {car.name} voltooid", msg)

    async def _send(self, service: str, title: str, message: str) -> None:
        domain, _, name = service.partition(".")
        if not name:
            domain, name = "notify", domain
        try:
            await self.hass.services.async_call(domain, name, {"title": title, "message": message}, blocking=False)
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Melding via %s mislukt", service, exc_info=True)

    async def async_refresh_history(self) -> None:
        self.history = await self.hass.async_add_executor_job(read_history, self.csv_dir, 40)
        self._notify()

    # -------------------------------------------------------------- slim laden
    def _refresh_slots(self) -> None:
        st = self._state(CONF_FORECAST) or self._state(CONF_PRICE)
        tz = dt_util.get_default_time_zone()
        self._slots = extract_slots(dict(st.attributes), tz) if st is not None else []

    def _reset_plan(self) -> None:
        sm = self.data["smart"]
        sm.update(deadline=None, charged_kwh=0.0, done=False, override=None)
        self._save()

    def _needed_kwh(self) -> tuple[float, str]:
        sm = self.data["smart"]
        car = self.car
        soc = self.car_soc(car)
        if soc is not None and car.capacity_kwh:
            need = (float(sm["target_soc"]) - soc) / 100 * car.capacity_kwh
            return max(need, 0.0), "soc"
        return max(float(sm["target_kwh"]) - float(sm["charged_kwh"]), 0.0), "kwh"

    def _compute_plan(self) -> bool | None:
        """Bepaal of er nu geladen moet worden. None = niet ingrijpen."""
        sm = self.data["smart"]
        now = dt_util.now()
        cat = self.category
        override = sm.get("override")

        if not sm["enabled"] or cat in (CAT_DISCONNECTED, CAT_UNKNOWN, CAT_ERROR):
            self.plan = self._preview_plan(now, "off" if not sm["enabled"] else "waiting")
            return None

        if not sm.get("deadline"):
            hh, mm = (int(x) for x in str(sm["ready_time"]).split(":")[:2])
            sm["deadline"] = next_occurrence(time(hh, mm), now).isoformat()
            self._save()
        deadline = dt_util.parse_datetime(sm["deadline"]) or now

        need, need_mode = self._needed_kwh()
        if cat == CAT_FINISHED and not override:
            sm["done"] = True
        if need <= 0.05 and not override:
            sm["done"] = True
        base = {
            "deadline": deadline.isoformat(),
            "needed_kwh": round(need, 2),
            "need_mode": need_mode,
            "charged_kwh": round(float(sm["charged_kwh"]), 2),
            "override": override,
        }
        if sm.get("done") and not override:
            self.plan = {**base, "mode": "done", "slots": self._slots_view(now, deadline, set())}
            return False

        needed_h = need / self.max_power if self.max_power > 0 else 0
        res = plan_cheapest(self._slots, now, deadline, needed_h, self.max_power)
        margin = timedelta(minutes=int(self.conf.get(CONF_SAFETY_MARGIN, 15)))
        time_left_h = (deadline - margin - now).total_seconds() / 3600

        if override == "on":
            desired, reason = True, "override_on"
        elif override == "off":
            desired, reason = False, "override_off"
        elif now >= deadline:
            desired, reason = True, "deadline_passed"
        elif not res.window:
            desired, reason = True, "no_prices"
        elif time_left_h <= needed_h:
            desired, reason = True, "deadline_close"
        elif res.charge_now:
            desired, reason = True, "cheap_now"
        else:
            desired, reason = False, "waiting_cheap"

        self.plan = {
            **base,
            "mode": "charging" if desired else "scheduled",
            "reason": reason,
            "needed_minutes": int(round(needed_h * 60)),
            "planned_minutes": int(round(res.planned_hours * 60)),
            "insufficient": res.insufficient,
            "expected_cost": res.expected_cost,
            "next_start": res.next_start.isoformat() if res.next_start else None,
            "slots": self._slots_view(now, deadline, res.selected),
        }
        return desired

    def _preview_plan(self, now: datetime, mode: str) -> dict[str, Any]:
        """Wat de planner zou doen met de huidige instellingen (zonder in te grijpen)."""
        sm = self.data["smart"]
        hh, mm = (int(x) for x in str(sm["ready_time"]).split(":")[:2])
        deadline = next_occurrence(time(hh, mm), now)
        need, need_mode = self._needed_kwh()
        if not sm.get("deadline"):
            # nog geen lopende planning: rekenen met het volledige laaddoel
            need = need if need_mode == "soc" else max(float(sm["target_kwh"]), 0.0)
        needed_h = need / self.max_power if self.max_power > 0 else 0
        res = plan_cheapest(self._slots, now, deadline, needed_h, self.max_power)
        return {
            "mode": mode,
            "preview": True,
            "deadline": deadline.isoformat(),
            "needed_kwh": round(need, 2),
            "need_mode": need_mode,
            "needed_minutes": int(round(needed_h * 60)),
            "insufficient": res.insufficient,
            "expected_cost": res.expected_cost,
            "next_start": res.next_start.isoformat() if res.next_start else None,
            "slots": self._slots_view(now, deadline, res.selected),
        }

    def _slots_view(self, now: datetime, deadline: datetime, selected: set) -> list[dict[str, Any]]:
        end = max(deadline, now + timedelta(hours=12))
        out = []
        for s in self._slots:
            if s.end <= now or s.start >= end:
                continue
            out.append(
                {
                    "start": s.start.isoformat(),
                    "end": s.end.isoformat(),
                    "price": s.price,
                    "selected": s.start in selected,
                    "after_deadline": s.start >= deadline,
                }
            )
        return out

    async def _evaluate(self) -> None:
        desired = self._compute_plan()
        if desired is not None:
            await self._control(desired)
        self._notify()

    async def _control(self, desired: bool) -> None:
        cat = self.category
        now = dt_util.now()
        if self._last_command and now - self._last_command < COMMAND_COOLDOWN:
            return
        if self._attempts >= 3:
            return  # laadpaal/auto reageert niet; wacht op een statuswissel
        if desired and cat in (CAT_CONNECTED, CAT_PAUSED) and self.can_start:
            await self._activate(True)
        elif not desired and cat == CAT_CHARGING and self.can_stop:
            await self._activate(False)

    async def _activate(self, start: bool) -> None:
        """Druk op knop / zet schakelaar / start script."""
        eid = self.conf.get(CONF_START) if start else (self.conf.get(CONF_STOP) or self.conf.get(CONF_START))
        if not eid:
            return
        domain = eid.split(".", 1)[0]
        same_entity = not start and eid == self.conf.get(CONF_START)
        if domain in ("button", "input_button"):
            if same_entity:
                return  # één knop kan niet aan én uit
            service = "press"
        elif domain in ("switch", "input_boolean", "light"):
            service = "turn_on" if start else "turn_off"
        elif domain == "script":
            if same_entity:
                return
            service = "turn_on"
        else:
            _LOGGER.warning("Domein %s wordt niet ondersteund voor bediening", domain)
            return
        self._last_command = dt_util.now()
        self._attempts += 1
        self._last_command_kind = "start" if start else "stop"
        _LOGGER.debug("%s laden via %s.%s(%s)", "Start" if start else "Stop", domain, service, eid)
        try:
            await self.hass.services.async_call(domain, service, {"entity_id": eid}, blocking=True)
        except Exception:  # noqa: BLE001
            _LOGGER.warning("%s.%s op %s mislukt", domain, service, eid, exc_info=True)

    # ------------------------------------------------------------ publieke API
    async def async_start(self) -> None:
        """Handmatig starten. Met slim laden aan: 'nu laden' tot de sessie eindigt."""
        if self.data["smart"]["enabled"]:
            self.data["smart"]["override"] = "on"
            self.data["smart"]["done"] = False
        self._last_command = None
        self._attempts = 0
        if self.can_start:
            await self._activate(True)
        self._save()
        await self._evaluate()

    async def async_stop(self) -> None:
        if self.data["smart"]["enabled"]:
            self.data["smart"]["override"] = "off"
        self._last_command = None
        self._attempts = 0
        if self.can_stop:
            await self._activate(False)
        self._save()
        await self._evaluate()

    async def async_clear_override(self) -> None:
        self.data["smart"]["override"] = None
        self._last_command = None
        self._save()
        await self._evaluate()

    async def async_select_car(self, name: str) -> None:
        for c in self.cars:
            if c.name.lower() == str(name).lower():
                self.data["car"] = c.name
                self._save()
                await self._evaluate()
                return
        raise ValueError(f"Onbekende auto: {name}")

    async def async_next_car(self) -> None:
        names = [c.name for c in self.cars]
        i = names.index(self.data["car"]) if self.data["car"] in names else -1
        await self.async_select_car(names[(i + 1) % len(names)])

    async def async_set_smart(
        self,
        enabled: bool | None = None,
        ready_time: str | time | None = None,
        target_kwh: float | None = None,
        target_soc: float | None = None,
    ) -> None:
        sm = self.data["smart"]
        if enabled is not None:
            sm["enabled"] = bool(enabled)
            sm["override"] = None
            if not enabled:
                sm.update(deadline=None, done=False)
        if ready_time is not None:
            if isinstance(ready_time, time):
                ready_time = ready_time.strftime("%H:%M")
            hh, mm = (int(x) for x in str(ready_time).split(":")[:2])
            sm["ready_time"] = f"{hh:02d}:{mm:02d}"
            sm["deadline"] = None  # opnieuw berekenen
        if target_kwh is not None:
            sm["target_kwh"] = max(float(target_kwh), 0.0)
            sm["done"] = False
        if target_soc is not None:
            sm["target_soc"] = min(max(int(target_soc), 0), 100)
            sm["done"] = False
        self._last_command = None
        self._save()
        await self._evaluate()

    async def _on_report_time(self, now: datetime) -> None:
        if now.day == int(self.conf.get(CONF_REPORT_DAY, 2)):
            await self.async_monthly_report(manual=False)

    async def async_monthly_report(self, car: str | None = None, month: str | None = None, manual: bool = True) -> None:
        await self.async_refresh_history()
        month = month or previous_month(dt_util.now().date())
        for c in self.cars:
            if car and c.name.lower() != car.lower():
                continue
            if not c.notify:
                continue
            stats = self.history["months"].get(c.slug, {}).get(month)
            title = f"{c.name} laadrapport {month}"
            if not stats or not stats["sessions"]:
                if manual:
                    await self._send(c.notify, title, f"Geen laadsessies gevonden voor {month}.")
                continue
            msg = (
                f"In totaal is er {stats['kwh']:.2f} kWh geladen voor een totaalbedrag van "
                f"€{stats['cost']:.2f}, met een gemiddelde laadprijs van €{stats['avg_price']:.3f} "
                f"per kWh, verdeeld over {stats['sessions']} laadsessies."
            )
            await self._send(c.notify, title, msg)

    # ------------------------------------------------------------- snapshot
    def entity_ids(self) -> dict[str, str]:
        """Eigen entiteiten per sleutel (status, session_energy, month_tesla, ...)."""
        reg = er.async_get(self.hass)
        prefix = f"{self.entry.entry_id}_"
        return {
            ent.unique_id.removeprefix(prefix): ent.entity_id
            for ent in er.async_entries_for_config_entry(reg, self.entry.entry_id)
        }

    def snapshot(self) -> dict[str, Any]:
        s = self.data["session"]
        now = dt_util.now()
        duration = None
        if s["active"] and s.get("start"):
            st = dt_util.parse_datetime(s["start"])
            if st:
                duration = int((now - st).total_seconds() // 60)
        slug_to_name = {c.slug: c.name for c in self.cars}
        sessions = [{**x, "car": slug_to_name.get(x["car"], x["car"])} for x in self.history["sessions"]]
        months = {slug_to_name.get(k, k): v for k, v in self.history["months"].items()}
        return {
            "entry_id": self.entry.entry_id,
            "name": self.name,
            "status": self.status,
            "can_start": self.can_start,
            "can_stop": self.can_stop,
            "has_start": bool(self.conf.get(CONF_START)),
            "has_stop": bool(self.conf.get(CONF_STOP) or self.conf.get(CONF_START)),
            "power_kw": self.power_kw,
            "max_power_kw": self.max_power,
            "price": self.price,
            "car": self.data["car"],
            "cars": [
                {
                    "name": c.name,
                    "soc": self.car_soc(c),
                    "capacity_kwh": c.capacity_kwh,
                    "has_notify": bool(c.notify),
                    "soc_entity": c.soc_entity,
                    "extra": c.extra,
                }
                for c in self.cars
            ],
            "session": {
                "active": s["active"],
                "start": s.get("start"),
                "kwh": round(float(s["kwh"]), 3),
                "cost": round(float(s["cost"]), 2),
                "duration_min": duration,
            },
            "last_session": self.data["last_session"],
            "smart": {
                "enabled": self.data["smart"]["enabled"],
                "ready_time": self.data["smart"]["ready_time"],
                "target_kwh": self.data["smart"]["target_kwh"],
                "target_soc": self.data["smart"]["target_soc"],
                "override": self.data["smart"]["override"],
                "has_forecast": bool(self._slots),
            },
            "plan": self.plan,
            "entities": self.entity_ids(),
            "sources": {
                k: self.conf.get(c)
                for k, c in (("status", CONF_STATUS), ("energy", CONF_ENERGY), ("power", CONF_POWER), ("price", CONF_PRICE))
            },
            "car_slugs": {c.name: c.slug for c in self.cars},
            "months": months,
            "sessions": sessions,
        }
