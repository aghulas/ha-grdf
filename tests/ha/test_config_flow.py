"""Config flow, reauth and options. One sign-in attempt per form submission, never more."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir

from custom_components.grdf_conso.api import GrdfAuthError, GrdfTemporaryError, Meter
from custom_components.grdf_conso.const import CONF_PCES, DOMAIN
from custom_components.grdf_conso.coordinator import FetchState, make_store, storage_key

from .common import ENTRY_DATA, PCE, make_entry

USER = {"username": " Compte@Exemple.invalid ", "password": "motdepasse-synth"}
PCE2 = "00000000000002"


class FakeSignIn:
    """Stands in for GrdfClient in the flow; counts the sign-in attempts."""

    def __init__(self) -> None:
        self.logins: list[tuple[str, str]] = []
        self.error: Exception | None = None
        self.meters = [Meter(PCE, "Site de test", True, "MM", "Active")]

    def build(self, _session: Any, username: str, password: str) -> FakeSignIn:
        self._pending = (username, password)
        return self

    async def async_login(self) -> None:
        self.logins.append(self._pending)
        if self.error is not None:
            raise self.error

    async def async_get_meters(self) -> list[Meter]:
        return self.meters


@pytest.fixture
def sign_in() -> Iterator[FakeSignIn]:
    fake = FakeSignIn()
    with (
        patch("custom_components.grdf_conso.config_flow.GrdfClient", side_effect=fake.build),
        patch("custom_components.grdf_conso.async_setup_entry", return_value=True),
    ):
        yield fake


async def _start(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return str(result["flow_id"])


async def test_single_meter_creates_the_entry(hass: HomeAssistant, sign_in: FakeSignIn) -> None:
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, USER)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Compte@Exemple.invalid"
    assert result["data"] == {
        "username": "Compte@Exemple.invalid",
        "password": "motdepasse-synth",
        CONF_PCES: {PCE: "Site de test"},
    }
    assert result["result"].unique_id == "compte@exemple.invalid"
    assert sign_in.logins == [("Compte@Exemple.invalid", "motdepasse-synth")]


async def test_several_meters_are_offered(hass: HomeAssistant, sign_in: FakeSignIn) -> None:
    sign_in.meters.append(Meter(PCE2, None, True, None, None))
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, USER)
    assert result["step_id"] == "meters"
    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_PCES: []})
    assert result["errors"] == {"base": "no_meter_selected"}
    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_PCES: [PCE2]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PCES] == {PCE2: None}
    assert len(sign_in.logins) == 1


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (GrdfAuthError("refused", ("errors.E0000004",), 401), "invalid_auth"),
        (GrdfAuthError("identify_refused"), "invalid_auth"),
        (GrdfAuthError("password_not_offered"), "extra_step_required"),
        (GrdfTemporaryError("unexpected login page"), "cannot_connect"),
    ],
)
async def test_one_attempt_per_submission(
    hass: HomeAssistant, sign_in: FakeSignIn, error: Exception, key: str
) -> None:
    sign_in.error = error
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, USER)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": key}
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(sign_in.logins) == 1  # shown to the user; nothing is retried behind their back

    sign_in.error = None
    result = await hass.config_entries.flow.async_configure(flow_id, USER)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(sign_in.logins) == 2


async def test_no_meter_aborts(hass: HomeAssistant, sign_in: FakeSignIn) -> None:
    sign_in.meters = []
    result = await hass.config_entries.flow.async_configure(await _start(hass), USER)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_meters"


async def test_same_account_twice_aborts_before_any_sign_in(
    hass: HomeAssistant, sign_in: FakeSignIn
) -> None:
    make_entry(unique_id="compte@exemple.invalid").add_to_hass(hass)
    result = await hass.config_entries.flow.async_configure(await _start(hass), USER)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert sign_in.logins == []


async def test_reauth_lifts_the_block_only_once_grdf_accepts(
    hass: HomeAssistant, sign_in: FakeSignIn, hass_storage: dict[str, Any]
) -> None:
    entry = make_entry(unique_id="compte@exemple.invalid")
    entry.add_to_hass(hass)
    store = make_store(hass, entry.entry_id)
    await store.async_save(FetchState(auth_refused=True, auth_reason="refused").as_dict())

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["username"] == ENTRY_DATA["username"]

    sign_in.error = GrdfAuthError("refused")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "encore-faux"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    assert len(sign_in.logins) == 1
    assert hass_storage[storage_key(entry.entry_id)]["data"]["auth_refused"] is True
    assert entry.data["password"] == ENTRY_DATA["password"]

    sign_in.error = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "nouveau-synth"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert sign_in.logins[-1] == (ENTRY_DATA["username"], "nouveau-synth")
    assert entry.data["password"] == "nouveau-synth"
    assert hass_storage[storage_key(entry.entry_id)]["data"]["auth_refused"] is False
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"auth_refused_{entry.entry_id}") is None
    assert entry.state is ConfigEntryState.LOADED


async def test_options(hass: HomeAssistant, sign_in: FakeSignIn) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"history_days": 365, "fetch_hour": 11, "dju_base": 19.5, "monthly": False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        "history_days": 365,
        "fetch_hour": 11,
        "dju_base": 19.5,
        "monthly": False,
    }
