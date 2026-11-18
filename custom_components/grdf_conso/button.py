"""Manual refresh. Guarded by the coordinator: no call when GRDF refused the credentials, and
not more often than every few minutes."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import GrdfConfigEntry
from .entity import GrdfEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: GrdfConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(GrdfRefresh(coordinator, pce, "refresh") for pce in coordinator.pces)


class GrdfRefresh(GrdfEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        await self.coordinator.async_manual_refresh()
