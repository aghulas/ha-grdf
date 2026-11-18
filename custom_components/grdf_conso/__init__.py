"""GRDF consommations — gas consumption published by GRDF, imported into Home Assistant statistics.

Read-only, and built never to lock the GRDF account: see coordinator.py and api/client.py.
"""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.storage import Store

from .api import GrdfClient
from .const import DOMAIN, ISSUE_AUTH_REFUSED
from .coordinator import (
    STORAGE_VERSION,
    FetchState,
    GrdfConfigEntry,
    GrdfCoordinator,
    async_create_auth_issue,
    storage_key,
)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: GrdfConfigEntry) -> bool:
    store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, storage_key(entry.entry_id))
    state = FetchState.from_dict(await store.async_load())
    if state.auth_refused:
        # A previous refusal is still pending: nothing is sent to GRDF until the user
        # re-authenticates (the reauth flow clears the flag).
        async_create_auth_issue(hass, entry)
        raise ConfigEntryAuthFailed("GRDF refused the credentials; re-authentication needed")
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_AUTH_REFUSED}_{entry.entry_id}")

    # A dedicated session: its cookie jar holds the GRDF session (never HA's shared session).
    session = async_create_clientsession(hass)
    client = GrdfClient(session, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    coordinator = GrdfCoordinator(hass, entry, client, store, state)
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GrdfConfigEntry) -> bool:
    # The schedule is cancelled by the entry's unload callbacks.
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: GrdfConfigEntry) -> None:
    await Store[dict[str, Any]](hass, STORAGE_VERSION, storage_key(entry.entry_id)).async_remove()
