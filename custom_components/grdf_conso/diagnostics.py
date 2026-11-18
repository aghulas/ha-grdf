"""Diagnostics, redacted: no password, e-mail, PCE number, name or address (CLAUDE.md rule 5).

Meters are numbered ("meter_1"…) instead of being identified by their PCE; their alias, chosen
by the user and possibly a name or an address, is not included.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .const import CONF_PCES
from .coordinator import GrdfConfigEntry, meter_to_dict


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GrdfConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    state = coordinator.state
    labels = {pce: f"meter_{n}" for n, pce in enumerate(entry.data[CONF_PCES], start=1)}
    data = coordinator.data
    auth_error = coordinator.client.auth_error
    return {
        "entry": {
            "version": entry.version,
            "meters": len(labels),
            "options": dict(entry.options),
        },
        "state": {
            "auth_refused": state.auth_refused,
            "auth_reason": state.auth_reason,
            "last_attempt": state.last_attempt.isoformat() if state.last_attempt else None,
            "last_success": state.last_success.isoformat() if state.last_success else None,
            "last_day": {
                labels.get(pce, "meter_removed"): day.isoformat()
                for pce, day in state.last_day.items()
            },
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "okta_keys": list(auth_error.okta_keys) if auth_error else [],
            "okta_status": auth_error.status if auth_error else None,
        },
        "meters": {
            labels[pce]: meter_to_dict(meter)
            for pce, meter in (data.meters.items() if data else [])
            if pce in labels
        },
    }
