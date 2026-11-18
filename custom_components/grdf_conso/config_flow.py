"""Config flow: sign in, pick the meters; reauth; options.

Every sign-in attempt here is triggered by the user submitting a form, and is made exactly once
(CLAUDE.md rule 1): an error is shown, and nothing is sent again until the user submits again.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import GrdfAuthError, GrdfClient, GrdfTemporaryError, Meter
from .const import (
    CONF_DJU_BASE,
    CONF_FETCH_HOUR,
    CONF_HISTORY_DAYS,
    CONF_MONTHLY,
    CONF_PCES,
    DEFAULT_DJU_BASE,
    DEFAULT_FETCH_HOUR,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_MONTHLY,
    DOMAIN,
)
from .coordinator import async_clear_auth_refused

_PASSWORD = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)
_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL)
        ),
        vol.Required(CONF_PASSWORD): _PASSWORD,
    }
)


def _error_key(err: GrdfAuthError | GrdfTemporaryError) -> str:
    if isinstance(err, GrdfTemporaryError):
        return "cannot_connect"
    return "extra_step_required" if err.reason == "password_not_offered" else "invalid_auth"


class GrdfConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._credentials: dict[str, str] = {}
        self._meters: list[Meter] = []

    async def _async_sign_in(self, username: str, password: str, *, meters: bool) -> list[Meter]:
        """One sign-in attempt with a throw-away session; optionally list the meters."""
        session: aiohttp.ClientSession = async_create_clientsession(self.hass, auto_cleanup=False)
        try:
            client = GrdfClient(session, username, password)
            await client.async_login()
            return await client.async_get_meters() if meters else []
        finally:
            session.detach()  # HA owns the connector; this only drops our cookie jar

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            await self.async_set_unique_id(username.lower())
            self._abort_if_unique_id_configured()
            try:
                self._meters = await self._async_sign_in(
                    username, user_input[CONF_PASSWORD], meters=True
                )
            except (GrdfAuthError, GrdfTemporaryError) as err:
                errors["base"] = _error_key(err)
            else:
                if not self._meters:
                    return self.async_abort(reason="no_meters")
                self._credentials = {
                    CONF_USERNAME: username,
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                }
                if len(self._meters) == 1:
                    return self._async_create([self._meters[0].pce])
                return await self.async_step_meters()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _USER_SCHEMA, {CONF_USERNAME: (user_input or {}).get(CONF_USERNAME)}
            ),
            errors=errors,
        )

    async def async_step_meters(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_PCES]:
                return self._async_create(user_input[CONF_PCES])
            errors["base"] = "no_meter_selected"
        labels = {m.pce: f"{m.alias} ({m.pce})" if m.alias else m.pce for m in self._meters}
        return self.async_show_form(
            step_id="meters",
            data_schema=vol.Schema(
                {vol.Required(CONF_PCES, default=list(labels)): cv.multi_select(labels)}
            ),
            errors=errors,
        )

    def _async_create(self, pces: list[str]) -> ConfigFlowResult:
        aliases = {m.pce: m.alias for m in self._meters}
        return self.async_create_entry(
            title=self._credentials[CONF_USERNAME],
            data={**self._credentials, CONF_PCES: {pce: aliases.get(pce) for pce in pces}},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._async_sign_in(
                    entry.data[CONF_USERNAME], user_input[CONF_PASSWORD], meters=False
                )
            except (GrdfAuthError, GrdfTemporaryError) as err:
                errors["base"] = _error_key(err)
            else:
                # GRDF accepted the new credentials: lift the block, then reload the entry.
                await async_clear_auth_refused(self.hass, entry.entry_id)
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): _PASSWORD}),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry[Any]) -> GrdfOptionsFlow:
        return GrdfOptionsFlow()


def _number(low: float, high: float, step: float, unit: str) -> Any:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=low,
            max=high,
            step=step,
            unit_of_measurement=unit,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


class GrdfOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_HISTORY_DAYS: int(user_input[CONF_HISTORY_DAYS]),
                    CONF_FETCH_HOUR: int(user_input[CONF_FETCH_HOUR]),
                    CONF_DJU_BASE: float(user_input[CONF_DJU_BASE]),
                    CONF_MONTHLY: bool(user_input[CONF_MONTHLY]),
                }
            )
        schema = vol.Schema(
            {
                vol.Required(CONF_HISTORY_DAYS): _number(1, 1095, 1, "d"),
                vol.Required(CONF_FETCH_HOUR): _number(0, 23, 1, "h"),
                vol.Required(CONF_DJU_BASE): _number(10, 25, 0.5, "°C"),
                vol.Required(CONF_MONTHLY): selector.BooleanSelector(),
            }
        )
        current = {
            CONF_HISTORY_DAYS: DEFAULT_HISTORY_DAYS,
            CONF_FETCH_HOUR: DEFAULT_FETCH_HOUR,
            CONF_DJU_BASE: DEFAULT_DJU_BASE,
            CONF_MONTHLY: DEFAULT_MONTHLY,
            **self.config_entry.options,
        }
        return self.async_show_form(
            step_id="init", data_schema=self.add_suggested_values_to_schema(schema, current)
        )
