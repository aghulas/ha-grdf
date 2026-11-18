"""Base entity: one device per meter (PCE)."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GrdfCoordinator, MeterData


class GrdfEntity(CoordinatorEntity[GrdfCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: GrdfCoordinator, pce: str, key: str) -> None:
        super().__init__(coordinator)
        self._pce = pce
        self._attr_translation_key = key
        self._attr_unique_id = f"{pce}_{key}"
        alias = coordinator.pces.get(pce)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, pce)},
            name=f"GRDF {alias}" if alias else "GRDF",
            manufacturer="GRDF",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def meter(self) -> MeterData | None:
        data = self.coordinator.data
        return data.meters.get(self._pce) if data else None

    @property
    def available(self) -> bool:
        # The values are "the last published by GRDF": they stay valid when a fetch fails.
        return self.meter is not None
