"""Sensors: the last values published by GRDF (history itself lives in statistics)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfTemperature, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Reading
from .const import PARIS
from .coordinator import GrdfConfigEntry, GrdfCoordinator, MeterData
from .entity import GrdfEntity

type Value = str | float | date | datetime | None


def _aware(moment: datetime | None) -> datetime | None:
    return moment.replace(tzinfo=PARIS) if moment and moment.tzinfo is None else moment


def _daily(field: Callable[[Reading], Value]) -> Callable[[MeterData], Value]:
    return lambda m: field(m.last_daily) if m.last_daily else None


def _published(field: Callable[[Reading], Value]) -> Callable[[MeterData], Value]:
    return lambda m: field(m.last_published) if m.last_published else None


def _daily_attrs(meter: MeterData) -> dict[str, Any]:
    r = meter.last_daily
    return {"gas_day": r.gas_day, "quality": r.quality} if r else {}


def _published_attrs(meter: MeterData) -> dict[str, Any]:
    r = meter.last_published
    if r is None:
        return {}
    return {
        "period_start": _aware(r.start),
        "period_end": _aware(r.end),
        "volume_m3": r.volume_m3,
        "index_start": r.index_start,
        "index_end": r.index_end,
        "conversion_factor": r.pcs,
        "quality": r.quality,
    }


@dataclass(frozen=True, kw_only=True)
class GrdfSensorDescription(SensorEntityDescription):
    value_fn: Callable[[MeterData], Value]
    attrs_fn: Callable[[MeterData], dict[str, Any]] | None = None


SENSORS: tuple[GrdfSensorDescription, ...] = (
    GrdfSensorDescription(
        key="last_day_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=0,
        value_fn=_daily(lambda r: r.energy_kwh),
        attrs_fn=_daily_attrs,
    ),
    GrdfSensorDescription(
        key="last_day_volume",
        device_class=SensorDeviceClass.GAS,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=1,
        value_fn=_daily(lambda r: r.volume_m3),
        attrs_fn=_daily_attrs,
    ),
    GrdfSensorDescription(
        key="billed_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=0,
        value_fn=_published(lambda r: r.energy_kwh),
        attrs_fn=_published_attrs,
    ),
    GrdfSensorDescription(
        key="outdoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda m: m.last_temperature[1] if m.last_temperature else None,
        attrs_fn=lambda m: {"day": m.last_temperature[0]} if m.last_temperature else {},
    ),
    GrdfSensorDescription(
        key="last_published_day",
        device_class=SensorDeviceClass.DATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_daily(lambda r: r.gas_day),
    ),
    GrdfSensorDescription(
        key="data_quality",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_daily(lambda r: r.quality),
    ),
    GrdfSensorDescription(
        key="last_reading",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_published(lambda r: _aware(r.end)),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: GrdfConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        GrdfSensor(coordinator, pce, description)
        for pce in coordinator.pces
        for description in SENSORS
    )


class GrdfSensor(GrdfEntity, SensorEntity):
    entity_description: GrdfSensorDescription

    def __init__(
        self, coordinator: GrdfCoordinator, pce: str, description: GrdfSensorDescription
    ) -> None:
        super().__init__(coordinator, pce, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Value:
        meter = self.meter
        return self.entity_description.value_fn(meter) if meter else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        meter, attrs_fn = self.meter, self.entity_description.attrs_fn
        return attrs_fn(meter) if meter and attrs_fn else None
