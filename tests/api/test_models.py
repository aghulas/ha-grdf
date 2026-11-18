from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from custom_components.grdf_conso.api import (
    degree_days,
    parse_daily,
    parse_meters,
    parse_published,
    parse_temperatures,
)

from .conftest import PCE, daily_row

START, END = date(2026, 1, 1), date(2026, 1, 3)


def test_parse_daily_filters_sorts_and_dedupes() -> None:
    payload = {
        PCE: {
            "releves": [
                daily_row("2026-01-02", 80),
                daily_row("2025-12-31", 1),  # the day before the range, always returned by GRDF
                daily_row("2026-01-01", "120.5", "10.7"),
                daily_row("2026-01-02", 90),  # correction: the last row wins
                daily_row("2026-01-04", 1),
                {"journeeGaziere": None, "energieConsomme": 3},
                {"journeeGaziere": "pas-une-date"},
                "bruit",
            ]
        },
        "99999999999999": {"releves": [daily_row("2026-01-01", 999)]},
    }
    rows = parse_daily(payload, PCE, START, END)
    assert [(r.gas_day, r.energy_kwh) for r in rows] == [
        (date(2026, 1, 1), 120.5),
        (date(2026, 1, 2), 90.0),
    ]
    first = rows[0]
    assert first.volume_m3 == 10.7
    assert first.start == datetime(2026, 1, 1, 6, tzinfo=timezone(timedelta(hours=1)))
    assert first.end is None
    assert (first.index_start, first.index_end, first.pcs) == (1000.0, 1010.0, 11.2)
    assert (first.quality, first.nature) == ("Mesuré", "Informative Journalier")


def test_parse_daily_tolerates_empty_or_odd_payloads() -> None:
    for payload in ([], {}, None, {PCE: None}, {PCE: {"releves": None}}, {PCE: {"releves": {}}}):
        assert parse_daily(payload, PCE, START, END) == []


def test_missing_values_stay_none() -> None:
    row = daily_row("2026-01-01", None, "")
    row["indexDebut"] = True
    row["pcs"] = "n/a"
    row["dateDebutReleve"] = "hier"
    (reading,) = parse_daily({PCE: {"releves": [row]}}, PCE, START, END)
    assert reading.energy_kwh is None
    assert reading.volume_m3 is None
    assert reading.index_start is None
    assert reading.pcs is None
    assert reading.start is None


def test_parse_published_sorts_by_period_end() -> None:
    def period(start: str, end: str | None, kwh: float) -> dict[str, object]:
        return {
            "journeeGaziere": None,
            "dateDebutReleve": f"{start}T06:00:00+01:00",
            "dateFinReleve": f"{end}T06:00:00+01:00" if end else None,
            "energieConsomme": kwh,
            "natureReleve": "Publiée",
        }

    payload = {
        PCE: {
            "releves": [
                period("2026-02-01", "2026-03-01", 2000),
                period("2026-01-01", "2026-02-01", 3000),
                period("2026-03-01", None, 1),
            ]
        }
    }
    rows = parse_published(payload, PCE)
    assert [r.energy_kwh for r in rows] == [3000.0, 2000.0]
    assert rows[0].gas_day is None
    assert parse_published([], PCE) == []


def test_parse_meters() -> None:
    payload = [
        {"pce": PCE, "alias": "A", "teleReleve": True, "frequenceReleve": "MM", "etat": "Active"},
        {"pce": PCE, "alias": "doublon"},
        {"pce": "GI000001", "alias": "", "teleReleve": "oui"},
        {"alias": "sans PCE"},
        "bruit",
    ]
    meters = parse_meters(payload)
    assert [(m.pce, m.alias, m.remote_reading, m.frequency, m.state) for m in meters] == [
        (PCE, "A", True, "MM", "Active"),
        ("GI000001", None, None, None, None),
    ]
    assert parse_meters({"erreur": 1}) == []


def test_temperatures_and_degree_days() -> None:
    temps = parse_temperatures(
        {"2026-01-02": "20.5", "2026-01-01": 3.25, "x": 1, "2026-01-03": None, "2026-01-04": "?"}
    )
    assert temps == {date(2026, 1, 1): 3.25, date(2026, 1, 2): 20.5}
    assert list(temps) == sorted(temps)
    assert degree_days(temps) == {date(2026, 1, 1): 14.75, date(2026, 1, 2): 0.0}
    assert degree_days(temps, base=21)[date(2026, 1, 2)] == 0.5
    assert parse_temperatures([]) == {}
