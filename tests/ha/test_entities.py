"""Sensors, connection-problem sensor, refresh button and diagnostics."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.grdf_conso.api import GrdfAuthError, GrdfTemporaryError, Reading
from custom_components.grdf_conso.const import DOMAIN
from custom_components.grdf_conso.diagnostics import async_get_config_entry_diagnostics

from .common import ENTRY_DATA, NOW, PCE, YESTERDAY, FakeClient, make_entry, move, setup

PARIS_WINTER = timezone(timedelta(hours=1))
BILLED = Reading(
    gas_day=None,
    start=datetime(2026, 2, 1, 6, tzinfo=PARIS_WINTER),
    end=datetime(2026, 3, 1, 6, tzinfo=PARIS_WINTER),
    energy_kwh=3210.0,
    volume_m3=290.5,
    index_start=1000.0,
    index_end=1290.5,
    pcs=11.05,
    quality="Mesuré",
    nature="Publiée",
)


def state_of(hass: HomeAssistant, platform: str, key: str) -> State:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{PCE}_{key}")
    assert entity_id is not None, key
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


async def press(hass: HomeAssistant) -> None:
    entity_id = state_of(hass, "button", "refresh").entity_id
    await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_sensors_show_the_last_published_values(
    hass: HomeAssistant, grdf: FakeClient
) -> None:
    grdf.published = [BILLED]
    await setup(hass, make_entry())

    energy = state_of(hass, "sensor", "last_day_energy")
    assert float(energy.state) == 100.0
    assert energy.attributes["gas_day"] == YESTERDAY
    assert energy.attributes["quality"] == "Mesuré"
    assert energy.attributes["unit_of_measurement"] == "kWh"
    assert energy.name == "GRDF Site de test Last day energy"
    assert float(state_of(hass, "sensor", "last_day_volume").state) == 10.0
    assert float(state_of(hass, "sensor", "outdoor_temperature").state) == 8.0
    assert state_of(hass, "sensor", "last_published_day").state == "2026-03-09"
    assert state_of(hass, "sensor", "data_quality").state == "Mesuré"

    billed = state_of(hass, "sensor", "billed_energy")
    assert float(billed.state) == 3210.0
    assert billed.attributes["period_end"] == BILLED.end
    assert billed.attributes["index_end"] == 1290.5
    assert state_of(hass, "sensor", "last_reading").state == "2026-03-01T05:00:00+00:00"

    assert state_of(hass, "binary_sensor", "problem").state == "off"
    assert PCE not in json.dumps([s.as_dict() for s in hass.states.async_all()], default=str)


async def test_missing_data_gives_unknown_not_errors(hass: HomeAssistant, grdf: FakeClient) -> None:
    grdf.energy.clear()
    grdf.temperature.clear()
    await setup(hass, make_entry())
    for key in ("last_day_energy", "billed_energy", "outdoor_temperature", "last_reading"):
        assert state_of(hass, "sensor", key).state == "unknown"
    assert state_of(hass, "binary_sensor", "problem").state == "off"


async def test_values_survive_a_restart_without_asking_grdf(
    hass: HomeAssistant, grdf: FakeClient
) -> None:
    grdf.published = [BILLED]
    entry = make_entry()
    await setup(hass, entry)
    grdf.calls.clear()

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert grdf.calls == []  # yesterday is already imported: no fetch at startup
    assert float(state_of(hass, "sensor", "last_day_energy").state) == 100.0
    assert float(state_of(hass, "sensor", "billed_energy").state) == 3210.0
    assert float(state_of(hass, "sensor", "outdoor_temperature").state) == 8.0


async def test_failed_fetch_raises_the_problem_and_keeps_the_values(
    hass: HomeAssistant, grdf: FakeClient, freezer: FrozenDateTimeFactory
) -> None:
    await setup(hass, make_entry())
    grdf.error = GrdfTemporaryError("HTTP 500")
    await move(hass, freezer, datetime(2026, 3, 11, 9, 31, tzinfo=UTC))

    problem = state_of(hass, "binary_sensor", "problem")
    assert problem.state == "on"
    assert problem.attributes["reauthentication_needed"] is False
    assert float(state_of(hass, "sensor", "last_day_energy").state) == 100.0


async def test_refused_authentication_shows_on_the_problem_sensor(
    hass: HomeAssistant, grdf: FakeClient
) -> None:
    grdf.error = GrdfAuthError("refused")
    await setup(hass, make_entry())
    problem = state_of(hass, "binary_sensor", "problem")
    assert problem.state == "on"
    assert problem.attributes["reauthentication_needed"] is True
    assert state_of(hass, "sensor", "last_day_energy").state == "unavailable"


async def test_refresh_button_is_rate_limited(
    hass: HomeAssistant, grdf: FakeClient, freezer: FrozenDateTimeFactory
) -> None:
    await setup(hass, make_entry())
    grdf.calls.clear()

    with pytest.raises(HomeAssistantError) as err:
        await press(hass)
    assert err.value.translation_key == "refresh_too_soon"
    assert grdf.calls == []

    freezer.move_to(NOW + timedelta(minutes=6))
    await press(hass)
    assert [c[0] for c in grdf.calls].count("daily") == 1


async def test_refresh_button_never_resends_a_refused_password(
    hass: HomeAssistant, grdf: FakeClient, freezer: FrozenDateTimeFactory
) -> None:
    grdf.error = GrdfAuthError("refused")
    await setup(hass, make_entry())
    assert len(grdf.calls) == 1

    freezer.move_to(NOW + timedelta(hours=1))
    with pytest.raises(HomeAssistantError) as err:
        await press(hass)
    assert err.value.translation_key == "reauth_needed"
    assert len(grdf.calls) == 1


async def test_diagnostics_are_redacted(hass: HomeAssistant, grdf: FakeClient) -> None:
    grdf.published = [BILLED]
    entry = make_entry(unique_id="compte@exemple.invalid")
    await setup(hass, entry)

    diag: dict[str, Any] = await async_get_config_entry_diagnostics(hass, entry)
    text = json.dumps(diag, default=str, ensure_ascii=False)
    for secret in (PCE, ENTRY_DATA["username"], ENTRY_DATA["password"], "Site de test", "exemple"):
        assert secret not in text

    assert diag["entry"]["meters"] == 1
    assert diag["state"]["last_day"] == {"meter_1": "2026-03-09"}
    assert diag["state"]["auth_refused"] is False
    assert diag["coordinator"] == {
        "last_update_success": True,
        "okta_keys": [],
        "okta_status": None,
    }
    assert diag["meters"]["meter_1"]["last_daily"]["energy_kwh"] == 100.0
    assert diag["meters"]["meter_1"]["last_published"]["end"] == "2026-03-01T06:00:00+01:00"


async def test_diagnostics_report_a_refusal(hass: HomeAssistant, grdf: FakeClient) -> None:
    grdf.error = GrdfAuthError("refused", ("errors.E0000004",), 401)
    grdf.auth_error = grdf.error
    entry = make_entry()
    await setup(hass, entry)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["state"]["auth_refused"] is True
    assert diag["state"]["auth_reason"] == "refused"
    assert diag["coordinator"]["okta_keys"] == ["errors.E0000004"]
    assert diag["coordinator"]["okta_status"] == 401
    assert diag["meters"] == {}
