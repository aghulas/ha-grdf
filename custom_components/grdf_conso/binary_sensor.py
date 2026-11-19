"""Connection problem: the last fetch failed, or GRDF refused the credentials."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import GrdfConfigEntry
from .entity import GrdfEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: GrdfConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(GrdfProblem(coordinator, pce, "problem") for pce in coordinator.pces)


class GrdfProblem(GrdfEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.state.auth_refused or not self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.coordinator.state
        return {
            "reauthentication_needed": state.auth_refused,
            "last_attempt": state.last_attempt,
            "last_success": state.last_success,
            "last_error": state.last_error,
            "last_error_at": state.last_error_at,
        }
