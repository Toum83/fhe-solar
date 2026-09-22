"""Capteurs FHE Solar."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FheConfigEntry
from .api import DashboardData
from .const import DOMAIN
from .coordinator import FheCoordinator


@dataclass(frozen=True, kw_only=True)
class FheSensorDescription(SensorEntityDescription):
    """Description d'un capteur FHE."""

    value_fn: Callable[[DashboardData], float | None]


SENSORS: tuple[FheSensorDescription, ...] = (
    FheSensorDescription(
        key="production_power",
        translation_key="production_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda d: d.production_power,
    ),
    FheSensorDescription(
        key="consumption_power",
        translation_key="consumption_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda d: d.consumption_power,
    ),
    # Compteurs du jour : remis à zéro par FHE à minuit. En total_increasing,
    # le tableau de bord Énergie de HA gère cette remise à zéro nativement.
    FheSensorDescription(
        key="production_today",
        translation_key="production_today",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda d: d.production_today,
    ),
    FheSensorDescription(
        key="consumption_today",
        translation_key="consumption_today",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda d: d.consumption_today,
    ),
    FheSensorDescription(
        key="self_consumption_today",
        translation_key="self_consumption_today",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda d: d.self_consumption_today,
    ),
    FheSensorDescription(
        key="grid_import_today",
        translation_key="grid_import_today",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda d: d.grid_import_today,
    ),
    FheSensorDescription(
        key="grid_export_today",
        translation_key="grid_export_today",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda d: d.grid_export_today,
    ),
    FheSensorDescription(
        key="self_consumption_rate",
        translation_key="self_consumption_rate",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        value_fn=lambda d: d.self_consumption_rate,
    ),
    FheSensorDescription(
        key="self_sufficiency_rate",
        translation_key="self_sufficiency_rate",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        value_fn=lambda d: d.self_sufficiency_rate,
    ),
    # Prévision FHE : unité non confirmée côté portail, exposée telle quelle.
    FheSensorDescription(
        key="forecast_today",
        translation_key="forecast_today",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.forecast_today,
    ),
    FheSensorDescription(
        key="forecast_tomorrow",
        translation_key="forecast_tomorrow",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.forecast_tomorrow,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FheConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crée les capteurs."""
    coordinator = entry.runtime_data
    async_add_entities(FheSensor(coordinator, entry, desc) for desc in SENSORS)


class FheSensor(CoordinatorEntity[FheCoordinator], SensorEntity):
    """Capteur alimenté par le coordinateur FHE."""

    entity_description: FheSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FheCoordinator,
        entry: FheConfigEntry,
        description: FheSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        data = coordinator.data
        identifier = data.device_mac or entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            name=data.device_label or "FHE Drive&Elec",
            manufacturer="FHE",
            model="Drive&Elec",
            configuration_url="https://fhesmart.fhe-france.com/dashboard",
        )

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        if self.entity_description.key not in ("production_power", "consumption_power"):
            return None
        sample = self.coordinator.data.last_sample
        return {"last_sample": sample.isoformat()} if sample else None
