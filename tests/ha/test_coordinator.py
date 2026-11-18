"""Coordinator: backfill, idempotent statistics, polling policy, stop on auth refusal."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.grdf_conso.api import GrdfAuthError, GrdfTemporaryError
from custom_components.grdf_conso.const import CONF_MONTHLY, DOMAIN
from custom_components.grdf_conso.coordinator import (
    FetchState,
    async_clear_auth_refused,
    storage_key,
)
from custom_components.grdf_conso.statistics import gas_day_start, statistic_id

from .common import (
    NOW,
    OLDEST,
    PCE,
    YESTERDAY,
    FakeClient,
    make_entry,
    move,
    reading,
    setup,
    stats,
)

ENERGY = statistic_id(PCE, "energy")


def reauth_flows(hass: HomeAssistant) -> list[Any]:
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    return [f for f in flows if f["context"]["source"] == "reauth"]


async def test_first_setup_backfills_statistics(hass: HomeAssistant, grdf: FakeClient) -> None:
    grdf.energy[date(2026, 3, 5)] = None  # a day GRDF has not measured
    entry = make_entry()
    await setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert grdf.calls == [
        ("daily", OLDEST, YESTERDAY),
        ("temperatures", YESTERDAY, 10),
        ("published", YESTERDAY - timedelta(days=396), YESTERDAY),
    ]
    rows = await stats(hass, ENERGY)
    assert len(rows) == 9  # 10 days, one without a value
    assert rows[0]["start"] == gas_day_start(OLDEST).timestamp()
    assert datetime.fromtimestamp(rows[0]["start"], UTC).hour == 5  # 06:00 Paris, winter
    assert [r["state"] for r in rows] == [100.0] * 9
    assert [r["sum"] for r in rows] == [100.0 * n for n in range(1, 10)]

    assert (await stats(hass, statistic_id(PCE, "volume")))[-1]["sum"] == pytest.approx(90.0)
    temps = await stats(hass, statistic_id(PCE, "temperature"))
    assert len(temps) == 10  # the extra day returned by GRDF is filtered out
    assert {r["mean"] for r in temps} == {8.0}
    assert (await stats(hass, statistic_id(PCE, "dju")))[-1]["sum"] == pytest.approx(100.0)

    coordinator = entry.runtime_data
    assert coordinator.data.meters[PCE].last_daily == reading(YESTERDAY, 100.0)
    assert coordinator.state.last_day == {PCE: YESTERDAY}
    assert coordinator.state.last_success is not None


async def test_reimport_is_idempotent_and_applies_corrections(
    hass: HomeAssistant, grdf: FakeClient
) -> None:
    entry = make_entry()
    await setup(hass, entry)
    before = await stats(hass, ENERGY)

    grdf.calls.clear()
    await entry.runtime_data.async_refresh()
    assert grdf.calls[0] == ("daily", YESTERDAY - timedelta(days=6), YESTERDAY)  # last 7 days
    assert await stats(hass, ENERGY) == before

    grdf.energy[date(2026, 3, 6)] = 130.0  # late correction by GRDF
    await entry.runtime_data.async_refresh()
    after = await stats(hass, ENERGY)
    assert len(after) == len(before) == 10
    assert [r["sum"] for r in after[:6]] == [r["sum"] for r in before[:6]]
    assert after[6]["state"] == 130.0
    assert [r["sum"] for r in after[6:]] == [r["sum"] + 30.0 for r in before[6:]]


async def test_hole_filled_later_keeps_sums_consistent(
    hass: HomeAssistant, grdf: FakeClient, freezer: FrozenDateTimeFactory
) -> None:
    del grdf.energy[date(2026, 3, 8)]
    entry = make_entry()
    await setup(hass, entry)
    assert (await stats(hass, ENERGY))[-1]["sum"] == 900.0

    grdf.energy[date(2026, 3, 8)] = 40.0
    await entry.runtime_data.async_refresh()
    rows = await stats(hass, ENERGY)
    assert [r["state"] for r in rows[-3:]] == [100.0, 40.0, 100.0]
    assert [r["sum"] for r in rows[-3:]] == [800.0, 840.0, 940.0]


async def test_catches_up_after_downtime(hass: HomeAssistant, grdf: FakeClient) -> None:
    entry = make_entry(history_days=60)
    entry.add_to_hass(hass)
    state = FetchState(last_day={PCE: YESTERDAY - timedelta(days=20)})
    with patch.object(FetchState, "from_dict", return_value=state):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert grdf.calls[0] == ("daily", YESTERDAY - timedelta(days=19), YESTERDAY)


async def test_refused_authentication_stops_everything(
    hass: HomeAssistant,
    grdf: FakeClient,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    grdf.error = GrdfAuthError("refused", ("errors.E0000004",), 401)
    entry = make_entry()
    await setup(hass, entry)

    assert len(grdf.calls) == 1  # the fetch stopped at the first refused call
    assert len(reauth_flows(hass)) == 1
    issue_id = f"auth_refused_{entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    assert hass_storage[storage_key(entry.entry_id)]["data"]["auth_refused"] is True

    # Neither a manual refresh, nor the schedule, nor days going by reach GRDF again.
    await entry.runtime_data.async_refresh()
    for days in (1, 2, 3):
        await move(hass, freezer, NOW + timedelta(days=days))
    assert len(grdf.calls) == 1

    # Nor does a restart: the entry refuses to set up and no client is even built.
    grdf.factory.reset_mock()
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    grdf.factory.assert_not_called()
    assert len(grdf.calls) == 1

    # Only the reauth flow (a user action) lifts the block.
    grdf.error = None
    await async_clear_auth_refused(hass, entry.entry_id)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.LOADED
    assert len(await stats(hass, ENERGY)) == 7  # it is now 13 March: 3 → 9 March are available


async def test_temporary_failure_waits_for_the_next_scheduled_fetch(
    hass: HomeAssistant, grdf: FakeClient, freezer: FrozenDateTimeFactory
) -> None:
    grdf.error = GrdfTemporaryError("GRDF portal answered its 403 error page")
    entry = make_entry()
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.last_update_success is False
    assert len(grdf.calls) == 1
    assert reauth_flows(hass) == []

    grdf.error = None
    await move(hass, freezer, NOW + timedelta(hours=8))  # same day: no retry
    assert len(grdf.calls) == 1

    # Next day: nothing before the fetch hour (10:00 Paris = 09:00 UTC), done by 10:30.
    await move(hass, freezer, datetime(2026, 3, 11, 8, 59, tzinfo=UTC))
    assert len(grdf.calls) == 1
    await move(hass, freezer, datetime(2026, 3, 11, 9, 31, tzinfo=UTC))
    assert len(grdf.calls) > 1
    assert entry.runtime_data.last_update_success is True
    assert len(await stats(hass, ENERGY)) == 9  # 1 → 9 March (the 10th is not published yet)


async def test_one_fetch_per_day(
    hass: HomeAssistant, grdf: FakeClient, freezer: FrozenDateTimeFactory
) -> None:
    entry = make_entry()
    await setup(hass, entry)
    grdf.calls.clear()
    for day in (11, 12, 13):
        grdf.fill(date(2026, 3, day - 1), date(2026, 3, day - 1))
        await move(hass, freezer, datetime(2026, 3, day, 9, 31, tzinfo=UTC))
        await move(hass, freezer, datetime(2026, 3, day, 20, 0, tzinfo=UTC))
    assert [c[0] for c in grdf.calls].count("daily") == 3
    assert entry.runtime_data.state.last_day == {PCE: date(2026, 3, 12)}


@pytest.mark.parametrize(
    "state",
    [
        FetchState(last_day={PCE: YESTERDAY}),  # already up to date
        FetchState(last_attempt=NOW - timedelta(hours=1)),  # tried recently (restart loop)
    ],
)
async def test_restart_does_not_fetch_again(
    hass: HomeAssistant, grdf: FakeClient, state: FetchState
) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch.object(FetchState, "from_dict", return_value=state):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.LOADED
    assert grdf.calls == []


async def test_before_the_fetch_hour_yesterday_is_not_expected_yet(
    hass: HomeAssistant, grdf: FakeClient, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(datetime(2026, 3, 10, 6, 0, tzinfo=UTC))  # 07:00 in Paris
    entry = make_entry()
    entry.add_to_hass(hass)
    state = FetchState(last_day={PCE: YESTERDAY - timedelta(days=1)})
    with patch.object(FetchState, "from_dict", return_value=state):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert grdf.calls == []


async def test_monthly_readings_can_be_disabled(hass: HomeAssistant, grdf: FakeClient) -> None:
    await setup(hass, make_entry(**{CONF_MONTHLY: False}))
    assert "published" not in [c[0] for c in grdf.calls]


async def test_unload_cancels_the_schedule_and_remove_deletes_the_state(
    hass: HomeAssistant,
    grdf: FakeClient,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    entry = make_entry()
    await setup(hass, entry)
    assert storage_key(entry.entry_id) in hass_storage
    grdf.calls.clear()
    await hass.config_entries.async_unload(entry.entry_id)
    await move(hass, freezer, NOW + timedelta(days=1))
    assert grdf.calls == []
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert storage_key(entry.entry_id) not in hass_storage


def test_gas_day_starts_at_six_paris_time_all_year() -> None:
    assert gas_day_start(date(2026, 1, 15)) == datetime(2026, 1, 15, 5, tzinfo=UTC)
    assert gas_day_start(date(2026, 7, 15)) == datetime(2026, 7, 15, 4, tzinfo=UTC)
    assert statistic_id("GI000001", "energy") == "grdf_conso:gi000001_energy"


def test_fetch_state_round_trip() -> None:
    state = FetchState(
        auth_refused=True,
        auth_reason="refused",
        last_attempt=NOW,
        last_success=NOW - timedelta(days=1),
        last_day={PCE: YESTERDAY},
    )
    assert FetchState.from_dict(state.as_dict()) == state
    assert FetchState.from_dict(None) == FetchState()


async def test_state_written_by_the_first_version_triggers_a_full_reimport(
    hass: HomeAssistant, grdf: FakeClient, hass_storage: dict[str, Any]
) -> None:
    """Version 1 missed the last day of each requested range: its `last_day` is not trusted."""
    entry = make_entry()
    hass_storage[storage_key(entry.entry_id)] = {
        "version": 1,
        "minor_version": 1,
        "key": storage_key(entry.entry_id),
        "data": {**FetchState(last_day={PCE: YESTERDAY}).as_dict()},
    }
    await setup(hass, entry)
    assert grdf.calls[0] == ("daily", OLDEST, YESTERDAY)  # the whole history, not 7 days
    assert len(await stats(hass, ENERGY)) == 10
    assert hass_storage[storage_key(entry.entry_id)]["version"] == 3
