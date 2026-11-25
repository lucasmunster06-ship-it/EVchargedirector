"""Tests voor de pure logica (geen Home Assistant nodig)."""
import importlib.util
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "logic", ROOT / "custom_components" / "ev_charge_director" / "logic.py"
)
logic = importlib.util.module_from_spec(spec)
import sys
sys.modules["logic"] = logic
spec.loader.exec_module(logic)

TZ = timezone(timedelta(hours=1))


def test_status_map_default():
    m = logic.parse_status_map("1 | disconnected | Klaar\n3 | charging | Opladen\n# x\nbad")
    assert m["3"].category == "charging"
    assert m["1"].label == "Klaar"
    assert logic.normalize_raw_status("3.0") == "3"
    assert logic.normalize_raw_status("Charging") == "charging"
    assert logic.validate_status_map("3 | laden") == "status_map_category"
    assert logic.validate_status_map("3 | charging") is None


def test_cars():
    cars = logic.parse_cars("Tesla | notify.lucas | sensor.tesla_battery | 75\nVolvo | richard\nGast")
    assert [c.name for c in cars] == ["Tesla", "Volvo", "Gast"]
    assert cars[1].notify == "notify.richard"
    assert cars[0].capacity_kwh == 75
    assert cars[2].slug == "gast"
    assert logic.validate_cars("Tesla | notify.x | sensor.y | abc") == "cars_capacity"
    assert logic.parse_cars("")[0].name == "Auto"


def _frank(n=24, start=datetime(2026, 11, 25, 0, 0, tzinfo=TZ)):
    return [
        {
            "from": (start + timedelta(hours=i)).isoformat(),
            "till": (start + timedelta(hours=i + 1)).isoformat(),
            "price": 0.30 - (0.01 * i if i < 12 else 0) ,
        }
        for i in range(n)
    ]


def test_extract_frank_and_nordpool_and_ct():
    slots = logic.extract_slots({"prices": _frank(), "friendly_name": "x"}, TZ)
    assert len(slots) == 24 and slots[0].hours == 1
    np = {
        "raw_today": [
            {"start": "2026-11-25T00:00:00+01:00", "end": "2026-11-25T00:15:00+01:00", "value": 21.5},
            {"start": "2026-11-25T00:15:00+01:00", "end": "2026-11-25T00:30:00+01:00", "value": 19.0},
        ]
    }
    s2 = logic.extract_slots(np, TZ)
    assert s2[0].price == 0.215  # ct -> €
    mapping = {"forecast": {"2026-11-25T00:00:00+01:00": 0.2, "2026-11-25T01:00:00+01:00": 0.1}}
    s3 = logic.extract_slots(mapping, TZ)
    assert len(s3) == 2 and s3[1].end - s3[1].start == timedelta(hours=1)
    # dubbel aangeleverd (prices + prices_today) -> ontdubbeld
    assert len(logic.extract_slots({"prices": _frank(), "prices_today": _frank()}, TZ)) == 24


def test_plan_cheapest():
    slots = logic.extract_slots({"prices": _frank()}, TZ)
    now = datetime(2026, 11, 25, 0, 30, tzinfo=TZ)
    deadline = datetime(2026, 11, 25, 7, 0, tzinfo=TZ)
    res = logic.plan_cheapest(slots, now, deadline, 2.0, 11)
    # goedkoopste binnen het venster zijn 06:00 en 05:00
    hours = sorted(s.hour for s in res.selected)
    assert hours == [5, 6]
    assert not res.charge_now and not res.insufficient
    assert res.next_start.hour == 5
    assert abs(res.expected_cost - round(11 * (0.25 + 0.24), 2)) < 0.01
    # te weinig tijd
    res2 = logic.plan_cheapest(slots, now, deadline, 10, 11)
    assert res2.insufficient and res2.charge_now


def test_next_occurrence():
    now = datetime(2026, 11, 25, 6, 50, tzinfo=TZ)
    assert logic.next_occurrence(time(7, 0), now).day == 26
    assert logic.next_occurrence(time(8, 0), now).day == 25


def test_csv_roundtrip(tmp_path):
    st = datetime(2026, 10, 3, 22, 0, tzinfo=TZ)
    logic.append_session_csv(str(tmp_path), "Tesla", st, st + timedelta(hours=3), 20.0, 4.0, 0.2, 180)
    logic.append_session_csv(str(tmp_path), "Tesla", st, st + timedelta(hours=1), 10.0, 3.0, 0.3, 60)
    # oud formaat uit het package (4 kolommen, komma's, €-teken)
    os.makedirs(tmp_path / "volvo")
    (tmp_path / "volvo" / "ev_laadsessies_2026-10.csv").write_text(
        "start sesie;kWh;Kosten;Gemiddelde Prijs/kWh\n2026-10-05 07:00;12,5;€ 2,50;0,2\n"
    )
    h = logic.read_history(str(tmp_path))
    t = h["months"]["tesla"]["2026-10"]
    assert t["sessions"] == 2 and t["kwh"] == 30.0 and t["cost"] == 7.0
    assert abs(t["avg_price"] - (20 * 0.2 + 10 * 0.3) / 30) < 0.001
    assert h["months"]["volvo"]["2026-10"]["cost"] == 2.5
    assert len(h["sessions"]) == 3
    assert logic.previous_month(date(2026, 1, 2)) == "2025-12"
    assert logic.format_duration(135) == "2 uur en 15 min"
