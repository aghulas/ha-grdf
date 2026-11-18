"""Test helpers for the Home Assistant side: a fake GRDF client and synthetic data."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.recorder import get_instance
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.grdf_conso.api import Reading
from custom_components.grdf_conso.const import CONF_HISTORY_DAYS, CONF_PCES, DOMAIN

PCE = "00000000000001"
NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)  # 13:00 in Paris, after the fetch hour
YESTERDAY = date(2026, 3, 9)
OLDEST = date(2026, 2, 28)  # history depth of 10 days in these tests
ENTRY_DATA = {
    "username": "compte@exemple.invalid",
    "password": "motdepasse-synth",
    CONF_PCES: {PCE: "Site de test"},
}


def make_entry(unique_id: str | None = None, **options: Any) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=unique_id,
        title="Compte de test",
        data=ENTRY_DATA,
        options={CONF_HISTORY_DAYS: 10, **options},
    )


class FakeClient:
    """Stands in for GrdfClient: serves `energy` / `temperature` and records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.error: Exception | None = None
        self.energy: dict[date, float | None] = {}
        self.temperature: dict[date, float] = {}
        self.published: list[Reading] = []
        self.auth_error: Exception | None = None
        self.factory: Any = None

    def fill(self, start: date, end: date, kwh: float = 100.0, temp: float = 8.0) -> None:
        day = start
        while day <= end:
            self.energy[day] = kwh
            self.temperature[day] = temp
            day += timedelta(days=1)

    def _call(self, *call: Any) -> None:
        self.calls.append(call)
        if self.error is not None:
            raise self.error

    async def async_get_daily(self, pce: str, start: date, end: date) -> list[Reading]:
        self._call("daily", start, end)
        return [
            reading(day, kwh) for day, kwh in sorted(self.energy.items()) if start <= day <= end
        ]

    async def async_get_temperatures(self, pce: str, end: date, days: int) -> dict[date, float]:
        self._call("temperatures", end, days)
        first = end - timedelta(days=days - 1)
        # Like GRDF, may return more than asked: the day before is included on purpose.
        return {d: t for d, t in self.temperature.items() if first - timedelta(days=1) <= d <= end}

    async def async_get_published(self, pce: str, start: date, end: date) -> list[Reading]:
        self._call("published", start, end)
        return self.published


def reading(day: date, kwh: float | None) -> Reading:
    return Reading(
        gas_day=day,
        start=None,
        end=None,
        energy_kwh=kwh,
        volume_m3=None if kwh is None else round(kwh / 10, 3),
        index_start=None,
        index_end=None,
        pcs=10.0,
        quality="Mesuré",
        nature="Informative Journalier",
    )


async def stats(hass: HomeAssistant, statistic_id: str) -> list[dict[str, Any]]:
    """All rows of an external statistic, once the recorder has processed its queue."""
    from homeassistant.components.recorder.statistics import statistics_during_period

    await async_wait_recording_done(hass)
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(1970, 1, 1, tzinfo=UTC),
        None,
        {statistic_id},
        "hour",
        None,
        {"state", "sum", "mean"},
    )
    return list(rows.get(statistic_id, []))


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def move(hass: HomeAssistant, freezer: FrozenDateTimeFactory, to: datetime) -> None:
    freezer.move_to(to)
    async_fire_time_changed(hass, to)
    await hass.async_block_till_done(wait_background_tasks=True)
