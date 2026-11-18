"""Pure functions and data classes: GRDF payloads → typed rows, degree days.

Same semantics as the MCP connector's `modeles.py` (normaliser_releve, dju).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class Meter:
    """A PCE (delivery point) attached to the account."""

    pce: str
    alias: str | None
    remote_reading: bool | None  # teleReleve: communicating meter
    frequency: str | None  # frequenceReleve
    state: str | None  # etat


@dataclass(frozen=True, slots=True)
class Reading:
    """One row: a gas day (daily, 06:00 → 06:00) or a published billing period (monthly)."""

    gas_day: date | None
    start: datetime | None
    end: datetime | None
    energy_kwh: float | None
    volume_m3: float | None
    index_start: float | None
    index_end: float | None
    pcs: float | None  # kWh per m³
    quality: str | None  # qualificationReleve: "Mesuré", "Estimé"…
    nature: str | None  # natureReleve: "Informative Journalier", "Publiée"…


def _num(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value[:10]) if isinstance(value, str) else None
    except ValueError:
        return None


def _datetime(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def parse_meters(payload: Any) -> list[Meter]:
    """`/api/e-accred/accreditations` → meters (rows without a PCE are skipped)."""
    meters: dict[str, Meter] = {}
    for row in payload if isinstance(payload, list) else []:
        pce = _str(row.get("pce")) if isinstance(row, dict) else None
        if pce is None:
            continue
        tele = row.get("teleReleve")
        meters.setdefault(
            pce,
            Meter(
                pce=pce,
                alias=_str(row.get("alias")),
                remote_reading=tele if isinstance(tele, bool) else None,
                frequency=_str(row.get("frequenceReleve")),
                state=_str(row.get("etat")),
            ),
        )
    return list(meters.values())


def parse_reading(row: dict[str, Any]) -> Reading:
    return Reading(
        gas_day=_date(row.get("journeeGaziere")),
        start=_datetime(row.get("dateDebutReleve")),
        end=_datetime(row.get("dateFinReleve")),
        energy_kwh=_num(row.get("energieConsomme")),
        volume_m3=_num(row.get("volumeBrutConsomme")),
        index_start=_num(row.get("indexDebut")),
        index_end=_num(row.get("indexFin")),
        pcs=_num(row.get("pcs")),
        quality=_str(row.get("qualificationReleve")),
        nature=_str(row.get("natureReleve")),
    )


def _rows(payload: Any, pce: str) -> list[dict[str, Any]]:
    # Keyed by PCE; GRDF answers `[]` instead of `{}` when there is nothing.
    entry = payload.get(pce) if isinstance(payload, dict) else None
    rows = entry.get("releves") if isinstance(entry, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def parse_daily(payload: Any, pce: str, start: date, end: date) -> list[Reading]:
    """Daily rows of `pce` within [start, end], one per gas day, sorted.

    GRDF also returns the day before `start`: filtered out. A day listed twice keeps its last row.
    """
    by_day: dict[date, Reading] = {}
    for row in _rows(payload, pce):
        reading = parse_reading(row)
        if reading.gas_day is not None and start <= reading.gas_day <= end:
            by_day[reading.gas_day] = reading
    return [by_day[day] for day in sorted(by_day)]


def parse_published(payload: Any, pce: str) -> list[Reading]:
    """Published (billing) rows of `pce`, sorted by period end; rows without an end are skipped."""
    readings = [r for r in map(parse_reading, _rows(payload, pce)) if r.end is not None]
    return sorted(readings, key=lambda r: r.end.timestamp() if r.end else 0.0)


def parse_temperatures(payload: Any) -> dict[date, float]:
    """`{ "YYYY-MM-DD": mean temperature }` → sorted mapping; unusable entries are skipped."""
    out: dict[date, float] = {}
    for key, value in payload.items() if isinstance(payload, dict) else []:
        day, temp = _date(key), _num(value)
        if day is not None and temp is not None:
            out[day] = temp
    return dict(sorted(out.items()))


def degree_days(temperatures: dict[date, float], base: float = 18.0) -> dict[date, float]:
    """Simple unified heating degree days: max(0, base − T), rounded to 0.01."""
    return {day: round(max(0.0, base - temp), 2) for day, temp in temperatures.items()}
