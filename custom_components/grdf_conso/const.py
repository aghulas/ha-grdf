"""Constants of the grdf_conso integration."""

from __future__ import annotations

from typing import Final
from zoneinfo import ZoneInfo

DOMAIN: Final = "grdf_conso"

# Config entry data
CONF_PCES: Final = "pces"  # {pce: alias or None}

# Options
CONF_HISTORY_DAYS: Final = "history_days"
CONF_FETCH_HOUR: Final = "fetch_hour"
CONF_DJU_BASE: Final = "dju_base"
CONF_MONTHLY: Final = "monthly"

DEFAULT_HISTORY_DAYS: Final = 730
DEFAULT_FETCH_HOUR: Final = 10
DEFAULT_DJU_BASE: Final = 18.0
DEFAULT_MONTHLY: Final = True

# GRDF dates (gas days, publication) are French local dates, whatever the HA time zone.
PARIS: Final = ZoneInfo("Europe/Paris")
GAS_DAY_START_HOUR: Final = 6

MAX_JITTER_SECONDS: Final = 30 * 60  # random delay added to the daily fetch hour
CORRECTION_DAYS: Final = 7  # days re-imported at each fetch (late corrections)
CHUNK_DAYS: Final = 90  # days per request when backfilling (GRDF's limits are not known)
PUBLISHED_DAYS: Final = 396  # 13 months of billing readings
MIN_ATTEMPT_GAP_HOURS: Final = 6  # no automatic fetch at startup sooner after an attempt

ISSUE_AUTH_REFUSED: Final = "auth_refused"
