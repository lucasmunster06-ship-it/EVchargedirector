"""Constants for EV Charge Director."""
from __future__ import annotations

DOMAIN = "ev_charge_director"
STORAGE_VERSION = 1

PLATFORMS = ["sensor", "binary_sensor", "switch", "select", "number", "time", "button"]

# --- bronnen -----------------------------------------------------------------
CONF_NAME = "name"
CONF_STATUS = "status_entity"
CONF_ENERGY = "energy_entity"
CONF_POWER = "power_entity"
CONF_PRICE = "price_entity"
CONF_FORECAST = "forecast_entity"

# --- bediening ---------------------------------------------------------------
CONF_START = "start_entity"
CONF_STOP = "stop_entity"
CONF_CAN_START = "can_start_entity"
CONF_CAN_STOP = "can_stop_entity"

# --- status ------------------------------------------------------------------
CONF_STATUS_MAP = "status_map"
CONF_END_CATEGORIES = "end_categories"

# --- auto's ------------------------------------------------------------------
CONF_CARS = "cars"

# --- slim laden --------------------------------------------------------------
CONF_MAX_POWER = "max_power_kw"
CONF_SAFETY_MARGIN = "safety_margin_min"

# --- opslag & rapport --------------------------------------------------------
CONF_CSV_ENABLED = "csv_enabled"
CONF_CSV_DIR = "csv_dir"
CONF_REPORT_DAY = "report_day"
CONF_REPORT_HOUR = "report_hour"

# Status categorieën. Elke ruwe statuswaarde van de laadpaal wordt op één
# van deze categorieën gemapt; de logica werkt alleen met categorieën.
CAT_DISCONNECTED = "disconnected"
CAT_CONNECTED = "connected"
CAT_CHARGING = "charging"
CAT_PAUSED = "paused"
CAT_FINISHED = "finished"
CAT_ERROR = "error"
CAT_UNKNOWN = "unknown"
CATEGORIES = [
    CAT_DISCONNECTED,
    CAT_CONNECTED,
    CAT_CHARGING,
    CAT_PAUSED,
    CAT_FINISHED,
    CAT_ERROR,
]

DEFAULT_STATUS_MAP = """1 | disconnected | Klaar om op te laden
2 | connected | Opladen starten
3 | charging | Opladen
4 | paused | Opladen gepauzeerd
5 | paused | Opladen gepauzeerd door auto
6 | finished | Opladen voltooid
9 | error | Laadpaal storing"""

DEFAULT_CARS = "Auto"

DEFAULTS = {
    CONF_STATUS_MAP: DEFAULT_STATUS_MAP,
    CONF_END_CATEGORIES: [CAT_DISCONNECTED],
    CONF_CARS: DEFAULT_CARS,
    CONF_MAX_POWER: 11.0,
    CONF_SAFETY_MARGIN: 15,
    CONF_CSV_ENABLED: True,
    CONF_CSV_DIR: "laadpaal",
    CONF_REPORT_DAY: 2,
    CONF_REPORT_HOUR: 4,
}

SERVICE_START = "start_charging"
SERVICE_STOP = "stop_charging"
SERVICE_SELECT_CAR = "select_car"
SERVICE_NEXT_CAR = "next_car"
SERVICE_SET_SMART = "set_smart_charging"
SERVICE_CLEAR_OVERRIDE = "clear_override"
SERVICE_MONTHLY_REPORT = "send_monthly_report"

CARD_FILENAME = "ev-charge-director-card.js"
CARD_URL = f"/{DOMAIN}/{CARD_FILENAME}"
