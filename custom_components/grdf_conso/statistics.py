"""Import of GRDF daily values into Home Assistant long-term statistics.

Each day is one hourly row placed at the start of the gas day (06:00 Europe/Paris). Imports are
idempotent: re-importing a day overwrites it, and the cumulative sums of every later row are
recomputed, so a late correction or a hole filled afterwards never leaves an inconsistent sum.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.recorder import get_instance

from .const import DOMAIN, GAS_DAY_START_HOUR, PARIS

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

KIND_ENERGY = "energy"
KIND_VOLUME = "volume"
KIND_TEMPERATURE = "temperature"
KIND_DJU = "dju"


def statistic_id(pce: str, kind: str) -> str:
    return f"{DOMAIN}:{pce.lower()}_{kind}"


def gas_day_start(day: date) -> datetime:
    """06:00 Europe/Paris on `day`, in UTC — always on the hour, as statistics require."""
    return datetime.combine(day, time(GAS_DAY_START_HOUR), PARIS).astimezone(UTC)


def _metadata(
    stat_id: str, name: str, unit: str, unit_class: str | None, *, is_sum: bool
) -> StatisticMetaData:
    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE if is_sum else StatisticMeanType.ARITHMETIC,
        has_sum=is_sum,
        name=name,
        source=DOMAIN,
        statistic_id=stat_id,
        unit_class=unit_class,
        unit_of_measurement=unit,
    )


async def async_import_sum(
    hass: HomeAssistant,
    stat_id: str,
    name: str,
    unit: str,
    unit_class: str | None,
    values: dict[date, float],
) -> int:
    """Import daily amounts (kWh, m³, degree days) as a cumulative statistic. Returns the number
    of rows written."""
    if not values:
        return 0
    new = {gas_day_start(day).timestamp(): value for day, value in values.items()}
    first = min(new)
    # One row per day: reading the whole series is cheap (≈ 365 rows per year).
    existing: dict[str, list[Any]] = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, _EPOCH, None, {stat_id}, "hour", None, {"state", "sum"}
    )
    base = 0.0
    merged: dict[float, float] = {}
    for row in existing.get(stat_id, []):
        if row["start"] < first:
            base = row.get("sum") or 0.0
        elif row.get("state") is not None:
            merged[row["start"]] = row["state"]
    merged.update(new)

    rows: list[StatisticData] = []
    total = base
    for start in sorted(merged):
        total += merged[start]
        rows.append(
            StatisticData(start=datetime.fromtimestamp(start, UTC), state=merged[start], sum=total)
        )
    async_add_external_statistics(
        hass, _metadata(stat_id, name, unit, unit_class, is_sum=True), rows
    )
    return len(rows)


def async_import_mean(
    hass: HomeAssistant,
    stat_id: str,
    name: str,
    unit: str,
    unit_class: str | None,
    values: dict[date, float],
) -> int:
    """Import daily mean values (temperature). Returns the number of rows written."""
    if not values:
        return 0
    rows = [
        StatisticData(start=gas_day_start(day), mean=value, min=value, max=value)
        for day, value in sorted(values.items())
    ]
    async_add_external_statistics(
        hass, _metadata(stat_id, name, unit, unit_class, is_sum=False), rows
    )
    return len(rows)
