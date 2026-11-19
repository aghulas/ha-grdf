"""Daily fetch of the GRDF data and import into statistics.

Polling policy (CLAUDE.md rules 1 and 2):
- one fetch per day, at the configured hour plus a random delay; at startup only if the last
  published day has not been imported yet and no attempt was made in the last hours;
- a temporary failure is not retried: the next attempt is the next scheduled fetch;
- an authentication refusal is persisted, stops the schedule and starts the reauth flow. Until
  the user re-authenticates — even across restarts — no request is sent to GRDF.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfTemperature, UnitOfVolume
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import GrdfAuthError, GrdfClient, GrdfTemporaryError, Reading, degree_days
from .const import (
    CHUNK_DAYS,
    CONF_DJU_BASE,
    CONF_FETCH_HOUR,
    CONF_HISTORY_DAYS,
    CONF_MONTHLY,
    CONF_PCES,
    CORRECTION_DAYS,
    DEFAULT_DJU_BASE,
    DEFAULT_FETCH_HOUR,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_MONTHLY,
    DOMAIN,
    INTERRUPTED_RETRY_MINUTES,
    ISSUE_AUTH_REFUSED,
    MANUAL_REFRESH_COOLDOWN_SECONDS,
    MAX_JITTER_SECONDS,
    MIN_ATTEMPT_GAP_HOURS,
    PARIS,
    PUBLISHED_DAYS,
    PUBLISHED_HISTORY_DAYS,
)
from .statistics import (
    KIND_BILLED_ENERGY,
    KIND_BILLED_VOLUME,
    KIND_DJU,
    KIND_ENERGY,
    KIND_TEMPERATURE,
    KIND_VOLUME,
    async_has_row,
    async_import_mean,
    async_import_sum,
    async_import_sum_at,
    statistic_id,
)

_LOGGER = logging.getLogger(__name__)

# 2: the first version missed the last gas day of every requested range; resetting `last_day`
#    makes the next fetch import the whole history again (idempotent) and fill those holes.
# 3: same again — on long ranges GRDF left out the last day of the oldest chunks.
# 4: `backfilled_days` reset — versions up to 0.1.0 did not record the oldest imported day, so a
#    history lost with the recorder database could not be detected; one full import records it.
STORAGE_VERSION = 4

type GrdfConfigEntry = ConfigEntry[GrdfCoordinator]


def storage_key(entry_id: str) -> str:
    return f"{DOMAIN}.{entry_id}"


class GrdfStore(Store[dict[str, Any]]):
    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: dict[str, Any]
    ) -> dict[str, Any]:
        if old_major_version < STORAGE_VERSION:
            old_data["last_day"] = {}
            old_data["backfilled_days"] = 0
        return old_data


def make_store(hass: HomeAssistant, entry_id: str) -> GrdfStore:
    return GrdfStore(hass, STORAGE_VERSION, storage_key(entry_id))


@dataclass(slots=True)
class FetchState:
    """What must survive a restart."""

    auth_refused: bool = False
    auth_reason: str | None = None
    last_attempt: datetime | None = None
    last_success: datetime | None = None
    last_day: dict[str, date] = field(default_factory=dict)  # per PCE: last gas day imported
    meters: dict[str, dict[str, Any]] = field(default_factory=dict)  # per PCE: last values shown
    backfilled_days: int = 0  # history depth covered by the last complete import
    first_day: dict[str, date] = field(default_factory=dict)  # per PCE: oldest gas day imported
    # "running" while a fetch is in progress: still "running" at startup means it was cut short.
    last_result: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> FetchState:
        data = data or {}

        def moment(key: str) -> datetime | None:
            return dt_util.parse_datetime(data[key]) if data.get(key) else None

        return cls(
            auth_refused=bool(data.get("auth_refused")),
            auth_reason=data.get("auth_reason"),
            last_attempt=moment("last_attempt"),
            last_success=moment("last_success"),
            last_day={p: date.fromisoformat(d) for p, d in (data.get("last_day") or {}).items()},
            meters=dict(data.get("meters") or {}),
            backfilled_days=int(data.get("backfilled_days") or 0),
            first_day={p: date.fromisoformat(d) for p, d in (data.get("first_day") or {}).items()},
            last_result=data.get("last_result"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "auth_refused": self.auth_refused,
            "auth_reason": self.auth_reason,
            "last_attempt": self.last_attempt.isoformat() if self.last_attempt else None,
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "last_day": {p: d.isoformat() for p, d in self.last_day.items()},
            "meters": self.meters,
            "backfilled_days": self.backfilled_days,
            "first_day": {p: d.isoformat() for p, d in self.first_day.items()},
            "last_result": self.last_result,
        }


async def async_clear_auth_refused(hass: HomeAssistant, entry_id: str) -> None:
    """Called by the reauth flow once new credentials have been accepted by GRDF."""
    store = make_store(hass, entry_id)
    state = FetchState.from_dict(await store.async_load())
    state.auth_refused, state.auth_reason = False, None
    await store.async_save(state.as_dict())
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_AUTH_REFUSED}_{entry_id}")


@callback
def async_create_auth_issue(hass: HomeAssistant, entry: ConfigEntry[Any]) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_AUTH_REFUSED}_{entry.entry_id}",
        is_fixable=False,
        is_persistent=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_AUTH_REFUSED,
        translation_placeholders={"title": entry.title},
    )


@dataclass(frozen=True, slots=True)
class MeterData:
    last_daily: Reading | None
    last_published: Reading | None
    last_temperature: tuple[date, float] | None


def _reading_to_dict(reading: Reading | None) -> dict[str, Any] | None:
    if reading is None:
        return None
    out = asdict(reading)
    for key in ("gas_day", "start", "end"):
        out[key] = out[key].isoformat() if out[key] else None
    return out


def _reading_from_dict(data: dict[str, Any] | None) -> Reading | None:
    if not data:
        return None
    return Reading(
        **{
            **data,
            "gas_day": date.fromisoformat(data["gas_day"]) if data.get("gas_day") else None,
            "start": datetime.fromisoformat(data["start"]) if data.get("start") else None,
            "end": datetime.fromisoformat(data["end"]) if data.get("end") else None,
        }
    )


def meter_to_dict(meter: MeterData) -> dict[str, Any]:
    temp = meter.last_temperature
    return {
        "last_daily": _reading_to_dict(meter.last_daily),
        "last_published": _reading_to_dict(meter.last_published),
        "last_temperature": [temp[0].isoformat(), temp[1]] if temp else None,
    }


def meter_from_dict(data: dict[str, Any]) -> MeterData:
    temp = data.get("last_temperature")
    return MeterData(
        last_daily=_reading_from_dict(data.get("last_daily")),
        last_published=_reading_from_dict(data.get("last_published")),
        last_temperature=(date.fromisoformat(temp[0]), float(temp[1])) if temp else None,
    )


@dataclass(frozen=True, slots=True)
class GrdfData:
    fetched_at: datetime
    meters: dict[str, MeterData]


def _chunks(start: date, end: date, size: int) -> Iterator[tuple[date, date]]:
    while start <= end:
        stop = min(end, start + timedelta(days=size - 1))
        yield start, stop
        start = stop + timedelta(days=1)


class GrdfCoordinator(DataUpdateCoordinator[GrdfData]):
    """One GRDF account: its meters, its schedule, its persisted state."""

    config_entry: GrdfConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: GrdfConfigEntry,
        client: GrdfClient,
        store: Store[dict[str, Any]],
        state: FetchState,
    ) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=None)
        self.client = client
        self.state = state
        self._store = store
        self._unsub: CALLBACK_TYPE | None = None
        # Show the last known values right after a restart, without asking GRDF again.
        restored = {p: meter_from_dict(m) for p, m in state.meters.items() if p in self.pces}
        if restored and state.last_success:
            self.data = GrdfData(fetched_at=state.last_success, meters=restored)

    # ---- options ---------------------------------------------------------------------------

    def _option(self, key: str, default: Any) -> Any:
        return self.config_entry.options.get(key, default)

    @property
    def pces(self) -> dict[str, str | None]:
        return dict(self.config_entry.data[CONF_PCES])

    # ---- schedule --------------------------------------------------------------------------

    @callback
    def async_start(self) -> None:
        """Schedule the daily fetch, and fetch now if yesterday is still missing."""
        self.config_entry.async_on_unload(self._async_cancel)
        self._async_schedule_next()
        if self._startup_fetch_needed():
            self.config_entry.async_create_background_task(
                self.hass, self.async_refresh(), f"{DOMAIN} startup fetch"
            )

    @property
    def _history_days(self) -> int:
        return int(self._option(CONF_HISTORY_DAYS, DEFAULT_HISTORY_DAYS))

    @property
    def _backfill_pending(self) -> bool:
        """The history depth was never imported in full, or the user has just increased it."""
        return self._history_days > self.state.backfilled_days

    def _startup_fetch_needed(self) -> bool:
        now = dt_util.now()
        # A deeper history asked in the options is a user action: it only waits for the
        # manual-refresh cooldown, not for the next day. A fetch cut short by a restart is
        # retried soon; a fetch that ended (even in failure) waits for hours.
        if self._backfill_pending and self.state.backfilled_days:
            gap = timedelta(seconds=MANUAL_REFRESH_COOLDOWN_SECONDS)
        elif self.state.last_result == "running":
            gap = timedelta(minutes=INTERRUPTED_RETRY_MINUTES)
        else:
            gap = timedelta(hours=MIN_ATTEMPT_GAP_HOURS)
        if self.state.last_attempt and now - self.state.last_attempt < gap:
            return False
        if self._backfill_pending:
            return True
        # The day GRDF should have published by now: yesterday once the fetch hour has passed.
        published = now.date() - timedelta(days=1 if now.hour >= self._fetch_hour else 2)
        return any(self.state.last_day.get(pce, date.min) < published for pce in self.pces)

    @property
    def _fetch_hour(self) -> int:
        return int(self._option(CONF_FETCH_HOUR, DEFAULT_FETCH_HOUR))

    @callback
    def _async_schedule_next(self) -> None:
        self._async_cancel()
        now = dt_util.now()
        target = now.replace(hour=self._fetch_hour, minute=0, second=0, microsecond=0)
        target += timedelta(seconds=random.uniform(0, MAX_JITTER_SECONDS))
        if target <= now:
            target += timedelta(days=1)
        self._unsub = async_track_point_in_time(self.hass, self._async_scheduled, target)

    @callback
    def _async_cancel(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    async def _async_scheduled(self, _now: datetime) -> None:
        self._unsub = None
        await self.async_refresh()
        if not self.state.auth_refused:
            self._async_schedule_next()

    async def async_manual_refresh(self) -> None:
        """Refresh asked by the user (button). Never sends a refused password again, and is
        rate-limited so that an impatient finger cannot hammer GRDF."""
        if self.state.auth_refused:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="reauth_needed")
        last = self.state.last_attempt
        if last and dt_util.now() - last < timedelta(seconds=MANUAL_REFRESH_COOLDOWN_SECONDS):
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="refresh_too_soon")
        await self.async_refresh()

    # ---- fetch -----------------------------------------------------------------------------

    async def _async_save(self) -> None:
        await self._store.async_save(self.state.as_dict())

    async def _async_update_data(self) -> GrdfData:
        if self.state.auth_refused:
            raise ConfigEntryAuthFailed("GRDF refused the credentials; re-authentication needed")
        self.state.last_attempt = dt_util.now()
        self.state.last_result = "running"
        await self._async_save()
        try:
            data = await self._async_fetch()
        except GrdfAuthError as err:
            self.state.auth_refused, self.state.auth_reason = True, err.reason
            self.state.last_result = "auth_refused"
            await self._async_save()
            self._async_cancel()
            async_create_auth_issue(self.hass, self.config_entry)
            raise ConfigEntryAuthFailed(str(err)) from err
        except GrdfTemporaryError as err:
            self.state.last_result = "failed"
            raise UpdateFailed(str(err)) from err
        finally:
            await self._async_save()
        self.state.last_success = data.fetched_at
        self.state.last_result = "ok"
        self.state.meters = {pce: meter_to_dict(meter) for pce, meter in data.meters.items()}
        await self._async_save()
        return data

    async def _async_fetch(self) -> GrdfData:
        end = dt_util.now(PARIS).date() - timedelta(days=1)  # GRDF publishes D+1
        depth = self._history_days
        oldest = end - timedelta(days=depth - 1)
        meters: dict[str, MeterData] = {}
        for pce, alias in self.pces.items():
            backfill = self._backfill_pending or await self._history_lost(pce)
            last = self.state.last_day.get(pce)
            start = oldest
            if last is not None and not backfill:
                # Late corrections, plus whatever was missed while HA was off.
                recent = end - timedelta(days=CORRECTION_DAYS - 1)
                start = max(oldest, min(recent, last + timedelta(days=1)))
            meters[pce] = await self._async_fetch_meter(pce, alias, start, end, backfill)
        self.state.backfilled_days = max(self.state.backfilled_days, depth)
        return GrdfData(fetched_at=dt_util.now(), meters=meters)

    async def _history_lost(self, pce: str) -> bool:
        """The oldest imported day is no longer in the statistics: the recorder database was
        reset (Home Assistant recreates it when it finds it corrupt). Import everything again."""
        first = self.state.first_day.get(pce)
        if first is None:
            return False
        if await async_has_row(self.hass, statistic_id(pce, KIND_ENERGY), first):
            return False
        _LOGGER.warning("GRDF history missing from the statistics: importing it again")
        return True

    async def _async_fetch_meter(
        self, pce: str, alias: str | None, start: date, end: date, backfill: bool
    ) -> MeterData:
        readings: list[Reading] = []
        temperatures: dict[date, float] = {}
        for first, last in _chunks(start, end, CHUNK_DAYS):
            readings += await self.client.async_get_daily(pce, first, last)
            days = (last - first).days + 1
            chunk = await self.client.async_get_temperatures(pce, last, days)
            temperatures.update({d: t for d, t in chunk.items() if first <= d <= last})
        published: list[Reading] = []
        if self._option(CONF_MONTHLY, DEFAULT_MONTHLY):
            published_days = PUBLISHED_HISTORY_DAYS if backfill else PUBLISHED_DAYS
            published = await self.client.async_get_published(
                pce, end - timedelta(days=published_days), end
            )

        label = f"GRDF {alias}" if alias else "GRDF"
        base = float(self._option(CONF_DJU_BASE, DEFAULT_DJU_BASE))
        energy = {
            r.gas_day: r.energy_kwh for r in readings if r.gas_day and r.energy_kwh is not None
        }
        volume = {r.gas_day: r.volume_m3 for r in readings if r.gas_day and r.volume_m3 is not None}
        await async_import_sum(
            self.hass,
            statistic_id(pce, KIND_ENERGY),
            f"{label} energy",
            UnitOfEnergy.KILO_WATT_HOUR,
            "energy",
            energy,
        )
        await async_import_sum(
            self.hass,
            statistic_id(pce, KIND_VOLUME),
            f"{label} volume",
            UnitOfVolume.CUBIC_METERS,
            "volume",
            volume,
        )
        async_import_mean(
            self.hass,
            statistic_id(pce, KIND_TEMPERATURE),
            f"{label} outdoor temperature",
            UnitOfTemperature.CELSIUS,
            "temperature",
            temperatures,
        )
        await async_import_sum(
            self.hass,
            statistic_id(pce, KIND_DJU),
            f"{label} degree days (base {base:g} °C)",
            "°C·d",
            None,
            degree_days(temperatures, base),
        )

        # Billing readings: one row per published period, at the start of the period. Kept apart
        # from the daily series (a whole month on one day would distort the daily charts).
        await async_import_sum_at(
            self.hass,
            statistic_id(pce, KIND_BILLED_ENERGY),
            f"{label} billed energy",
            UnitOfEnergy.KILO_WATT_HOUR,
            "energy",
            {r.start: r.energy_kwh for r in published if r.start and r.energy_kwh is not None},
        )
        await async_import_sum_at(
            self.hass,
            statistic_id(pce, KIND_BILLED_VOLUME),
            f"{label} billed volume",
            UnitOfVolume.CUBIC_METERS,
            "volume",
            {r.start: r.volume_m3 for r in published if r.start and r.volume_m3 is not None},
        )

        days_seen = [r.gas_day for r in readings if r.gas_day]
        if days_seen:
            self.state.last_day[pce] = max(days_seen)
            if backfill:
                self.state.first_day[pce] = min(days_seen)
        _LOGGER.debug(
            "GRDF fetch: %d daily rows, %d temperatures, %d published rows",
            len(readings),
            len(temperatures),
            len(published),
        )
        return MeterData(
            last_daily=readings[-1] if readings else None,
            last_published=published[-1] if published else None,
            last_temperature=max(temperatures.items()) if temperatures else None,
        )
