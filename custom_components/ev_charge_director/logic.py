"""Pure logica zonder Home Assistant-afhankelijkheden (los te testen).

- status-mapping en auto-lijst parsen
- prijsvoorspelling uit willekeurige sensor-attributen halen
- goedkoopste laadmomenten plannen
- CSV-historie lezen en per maand optellen
"""
from __future__ import annotations

import csv
import io
import math
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from statistics import median
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------

VALID_CATEGORIES = {
    "disconnected",
    "connected",
    "charging",
    "paused",
    "finished",
    "error",
}


@dataclass
class StatusEntry:
    raw: str
    category: str
    label: str


def parse_status_map(text: str | None) -> dict[str, StatusEntry]:
    """Parse regels als ``3 | charging | Opladen``.

    De eerste kolom is de ruwe waarde van de statussensor (hoofdletterongevoelig),
    de tweede de categorie, de derde (optioneel) het label dat je wilt zien.
    Regels die met # beginnen worden genegeerd.
    """
    result: dict[str, StatusEntry] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2 or not parts[0]:
            continue
        cat = parts[1].lower()
        if cat not in VALID_CATEGORIES:
            continue
        label = parts[2] if len(parts) > 2 and parts[2] else parts[0]
        result[parts[0].lower()] = StatusEntry(parts[0], cat, label)
    return result


def validate_status_map(text: str) -> str | None:
    """Geef een foutcode terug als de mapping onbruikbaar is."""
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2 or not parts[0]:
            return "status_map_format"
        if parts[1].lower() not in VALID_CATEGORIES:
            return "status_map_category"
    if not parse_status_map(text):
        return "status_map_empty"
    return None


def normalize_raw_status(value: Any) -> str:
    """'3.0' en '3' moeten hetzelfde zijn."""
    s = str(value).strip()
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except (TypeError, ValueError):
        pass
    return s.lower()


# ---------------------------------------------------------------------------
# Auto's
# ---------------------------------------------------------------------------


@dataclass
class Car:
    name: str
    notify: str | None = None
    soc_entity: str | None = None
    capacity_kwh: float | None = None

    @property
    def slug(self) -> str:
        return car_slug(self.name)


def car_slug(name: str) -> str:
    """Zelfde mapnaam als het oude package: lower + spaties -> _."""
    return re.sub(r"\s+", "_", name.strip().lower())


def parse_cars(text: str | None) -> list[Car]:
    """Regels als ``Tesla | notify.lucas | sensor.tesla_battery | 75``."""
    cars: list[Car] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        name = parts[0]
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        notify = parts[1] if len(parts) > 1 and parts[1] else None
        if notify and "." not in notify:
            notify = f"notify.{notify}"
        soc = parts[2] if len(parts) > 2 and parts[2] else None
        cap = None
        if len(parts) > 3 and parts[3]:
            try:
                cap = float(parts[3].replace(",", "."))
            except ValueError:
                cap = None
        cars.append(Car(name, notify, soc, cap))
    if not cars:
        cars.append(Car("Auto"))
    return cars


def validate_cars(text: str) -> str | None:
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if not parts[0]:
            return "cars_format"
        if len(parts) > 1 and parts[1] and not parts[1].startswith("notify.") and "." in parts[1]:
            return "cars_notify"
        if len(parts) > 2 and parts[2] and "." not in parts[2]:
            return "cars_soc"
        if len(parts) > 3 and parts[3]:
            try:
                if float(parts[3].replace(",", ".")) <= 0:
                    return "cars_capacity"
            except ValueError:
                return "cars_capacity"
    return None


# ---------------------------------------------------------------------------
# Prijsvoorspelling
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime
    price: float

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600


START_KEYS = (
    "start", "from", "startsAt", "starts_at", "start_time", "startTime", "time",
    "datetime", "date_from", "dateFrom", "begin", "period_start", "hour", "timestamp",
)
END_KEYS = (
    "end", "till", "to", "endsAt", "ends_at", "end_time", "endTime", "date_till",
    "dateTill", "until", "period_end",
)
PRICE_KEYS = (
    "price", "value", "total", "price_all_in", "all_in", "allInPrice", "priceIncl",
    "price_incl", "marketPrice", "market_price", "electricity_price", "price_eur",
    "price_ct_per_kwh", "tariff",
)


def _parse_dt(value: Any, tz) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)) and value > 10**9:
        dt = datetime.fromtimestamp(value / 1000 if value > 10**11 else value, tz)
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            v = float(value.replace(",", ".").replace("€", "").strip())
            return v if math.isfinite(v) else None
        except ValueError:
            return None
    return None


def _pick(d: dict, keys: Iterable[str]) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    lower = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        v = lower.get(k.lower())
        if v is not None:
            return v
    return None


def _points_from_list(items: list, tz) -> list[tuple[datetime, datetime | None, float]]:
    pts = []
    for it in items:
        if not isinstance(it, dict):
            continue
        st = _parse_dt(_pick(it, START_KEYS), tz)
        pr = _num(_pick(it, PRICE_KEYS))
        if st is None or pr is None:
            continue
        en = _parse_dt(_pick(it, END_KEYS), tz)
        pts.append((st, en, pr))
    return pts


def _points_from_mapping(d: dict, tz) -> list[tuple[datetime, datetime | None, float]]:
    pts = []
    for k, v in d.items():
        st = _parse_dt(k, tz)
        pr = _num(v)
        if st is not None and pr is not None:
            pts.append((st, None, pr))
    return pts


def extract_slots(attributes: dict[str, Any] | None, tz) -> list[Slot]:
    """Haal prijsblokken uit sensor-attributen, ongeacht de leverancier.

    Werkt met o.a. Frank Energie (``prices: [{from, till, price}]``),
    Nordpool (``raw_today``/``raw_tomorrow: [{start, end, value}]``),
    ENTSO-e (``prices: [{time, price}]``), EnergyZero/easyEnergy-templates en
    mappings als ``{"2026-11-25T13:00:00+01:00": 0.21}``.
    """
    if not attributes:
        return []
    points: list[tuple[datetime, datetime | None, float]] = []

    def scan(value: Any, depth: int) -> None:
        if depth > 2:
            return
        if isinstance(value, list) and value and isinstance(value[0], dict):
            points.extend(_points_from_list(value, tz))
        elif isinstance(value, dict):
            mp = _points_from_mapping(value, tz)
            if mp:
                points.extend(mp)
            else:
                for sub in value.values():
                    scan(sub, depth + 1)

    for value in attributes.values():
        scan(value, 0)

    if not points:
        return []

    # dubbele starttijden samenvoegen (bijv. 'prices' én 'prices_today')
    by_start: dict[datetime, tuple[datetime | None, float]] = {}
    for st, en, pr in points:
        if st not in by_start or (by_start[st][0] is None and en is not None):
            by_start[st] = (en, pr)
    starts = sorted(by_start)

    gaps = [
        (b - a).total_seconds() for a, b in zip(starts, starts[1:], strict=False) if (b - a).total_seconds() > 0
    ]
    default_len = timedelta(seconds=min(gaps)) if gaps else timedelta(hours=1)

    prices = [by_start[s][1] for s in starts]
    # ct/kWh of €/MWh herkennen: geen enkel tarief kost structureel > €3/kWh
    med = median(prices)
    factor = 1.0
    if abs(med) > 30:
        factor = 0.001  # €/MWh
    elif abs(med) > 3:
        factor = 0.01  # ct/kWh

    slots: list[Slot] = []
    for i, st in enumerate(starts):
        en, pr = by_start[st]
        if en is None or en <= st:
            nxt = starts[i + 1] if i + 1 < len(starts) else None
            en = nxt if nxt is not None and nxt - st <= default_len * 4 else st + default_len
        slots.append(Slot(st, en, round(pr * factor, 5)))
    return slots


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


@dataclass
class PlanResult:
    selected: set[datetime] = field(default_factory=set)
    window: list[tuple[Slot, float]] = field(default_factory=list)  # (slot, uren binnen venster)
    planned_hours: float = 0.0
    available_hours: float = 0.0
    insufficient: bool = False
    expected_cost: float = 0.0
    next_start: datetime | None = None
    charge_now: bool = False


def plan_cheapest(
    slots: list[Slot],
    now: datetime,
    deadline: datetime,
    needed_hours: float,
    power_kw: float,
) -> PlanResult:
    """Kies de goedkoopste blokken tussen nu en de deadline."""
    res = PlanResult()
    for s in slots:
        st = max(s.start, now)
        en = min(s.end, deadline)
        if en > st:
            res.window.append((s, (en - st).total_seconds() / 3600))
    res.available_hours = sum(h for _, h in res.window)
    if needed_hours <= 0:
        return res

    remaining = needed_hours
    for s, h in sorted(res.window, key=lambda x: (x[0].price, x[0].start)):
        if remaining <= 1e-6:
            break
        res.selected.add(s.start)
        used = min(h, remaining)
        res.expected_cost += used * power_kw * s.price
        res.planned_hours += used
        remaining -= used

    res.insufficient = remaining > 1e-6
    res.expected_cost = round(res.expected_cost, 2)
    for s, _ in sorted(res.window, key=lambda x: x[0].start):
        if s.start in res.selected:
            if s.start <= now < s.end:
                res.charge_now = True
            if res.next_start is None:
                res.next_start = max(s.start, now)
    return res


def next_occurrence(t: time, now: datetime, min_ahead: timedelta = timedelta(minutes=30)) -> datetime:
    """Eerstvolgende moment met kloktijd ``t`` dat minstens ``min_ahead`` weg is."""
    cand = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
    if cand - now < min_ahead:
        cand += timedelta(days=1)
    return cand


# ---------------------------------------------------------------------------
# CSV historie
# ---------------------------------------------------------------------------

CSV_HEADER = "start sessie;kWh;Kosten;Gemiddelde Prijs/kWh;Einde;Duur (min)"
FILE_RE = re.compile(r"^ev_laadsessies_(\d{4})-(\d{2})\.csv$")


def csv_row(start: datetime, end: datetime, kwh: float, cost: float, avg: float, duration_min: int) -> str:
    return (
        f"{start.strftime('%Y-%m-%d %H:%M')};{kwh:.3f};{cost:.2f};{avg:.3f};"
        f"{end.strftime('%Y-%m-%d %H:%M')};{duration_min}"
    )


def append_session_csv(base_dir: str, car_name: str, start: datetime, end: datetime,
                       kwh: float, cost: float, avg: float, duration_min: int) -> str:
    d = os.path.join(base_dir, car_slug(car_name))
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"ev_laadsessies_{start.strftime('%Y-%m')}.csv")
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as f:
        if new:
            f.write(CSV_HEADER + "\n")
        f.write(csv_row(start, end, kwh, cost, avg, duration_min) + "\n")
    return path


def _clean_num(s: str) -> float | None:
    s = (s or "").replace("\r", "").replace("€", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def read_history(base_dir: str, max_sessions: int = 30) -> dict[str, Any]:
    """Lees alle CSV's: maandtotalen per auto-map en de laatste sessies."""
    months: dict[str, dict[str, dict[str, float]]] = {}
    sessions: list[dict[str, Any]] = []
    if not os.path.isdir(base_dir):
        return {"months": months, "sessions": sessions}
    for slug in sorted(os.listdir(base_dir)):
        cdir = os.path.join(base_dir, slug)
        if not os.path.isdir(cdir):
            continue
        for fn in sorted(os.listdir(cdir)):
            m = FILE_RE.match(fn)
            if not m:
                continue
            month = f"{m.group(1)}-{m.group(2)}"
            try:
                with open(os.path.join(cdir, fn), encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError:
                continue
            agg = {"kwh": 0.0, "cost": 0.0, "sessions": 0, "weighted": 0.0}
            reader = csv.reader(io.StringIO(content), delimiter=";")
            for i, row in enumerate(reader):
                if i == 0 or len(row) < 4:
                    continue
                kwh, cost, avg = _clean_num(row[1]), _clean_num(row[2]), _clean_num(row[3])
                if kwh is None or cost is None:
                    continue
                avg = avg if avg is not None else (cost / kwh if kwh else 0)
                agg["kwh"] += kwh
                agg["cost"] += cost
                agg["weighted"] += kwh * avg
                agg["sessions"] += 1
                dur = None
                if len(row) > 5:
                    d = _clean_num(row[5])
                    dur = int(d) if d is not None else None
                sessions.append({
                    "car": slug,
                    "start": row[0].strip(),
                    "end": row[4].strip() if len(row) > 4 else None,
                    "kwh": round(kwh, 3),
                    "cost": round(cost, 2),
                    "avg_price": round(avg, 3),
                    "duration_min": dur,
                })
            months.setdefault(slug, {})[month] = {
                "kwh": round(agg["kwh"], 2),
                "cost": round(agg["cost"], 2),
                "sessions": agg["sessions"],
                "avg_price": round(agg["weighted"] / agg["kwh"], 3) if agg["kwh"] > 0 else 0.0,
            }
    sessions.sort(key=lambda s: s["start"], reverse=True)
    return {"months": months, "sessions": sessions[:max_sessions]}


def previous_month(today: date) -> str:
    first = today.replace(day=1)
    return (first - timedelta(days=1)).strftime("%Y-%m")


def format_duration(minutes: int) -> str:
    h, m = divmod(max(int(minutes), 0), 60)
    if h:
        return f"{h} uur" + (f" en {m} min" if m else "")
    return f"{m} min"
