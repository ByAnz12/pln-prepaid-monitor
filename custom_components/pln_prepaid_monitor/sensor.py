"""Entity sensor kanonik per Energy Source (Milestone 1).

Satu Energy Source menghasilkan sampai lima sensor, semuanya sudah dalam
satuan seragam apa pun satuan aslinya di perangkat:

* energi (kWh)   - selalu dibuat selama ada sensor kWh atau sensor daya
* daya (W), tegangan (V), arus (A), frekuensi (Hz) - dibuat bila dipetakan
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    ATTR_ACCUMULATOR_OFFSET,
    ATTR_ACCUMULATOR_ZERO_POINT,
    ATTR_CONSUMED_SINCE_START,
    ATTR_DIPS_DETECTED,
    ATTR_LAST_RESET_AT,
    ATTR_LAST_RESET_FROM,
    ATTR_LAST_RESET_TO,
    ATTR_RESETS_DETECTED,
    ATTR_SOURCE_ENTITY_ID,
    ATTR_SOURCE_RAW_VALUE,
    ATTR_SOURCE_STATE_CLASS,
    ATTR_SOURCE_UNIT,
    ATTR_UNIT_CONVERSION_FACTOR,
    CHANNEL_CONF_KEYS,
    CHANNEL_CURRENT,
    CHANNEL_ENERGY,
    CHANNEL_FREQUENCY,
    CHANNEL_POWER,
    CHANNEL_VOLTAGE,
)
from .coordinator import PlnRuntimeData, SourceRuntime
from .entity import PlnSourceEntity

MEASUREMENT_CHANNELS: dict[str, dict[str, Any]] = {
    CHANNEL_POWER: {
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "precision": 1,
    },
    CHANNEL_VOLTAGE: {
        "device_class": SensorDeviceClass.VOLTAGE,
        "unit": UnitOfElectricPotential.VOLT,
        "precision": 1,
    },
    CHANNEL_CURRENT: {
        "device_class": SensorDeviceClass.CURRENT,
        "unit": UnitOfElectricCurrent.AMPERE,
        "precision": 2,
    },
    CHANNEL_FREQUENCY: {
        "device_class": SensorDeviceClass.FREQUENCY,
        "unit": UnitOfFrequency.HERTZ,
        "precision": 2,
    },
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Buat sensor untuk setiap Energy Source yang aktif."""
    runtime_data: PlnRuntimeData = entry.runtime_data

    for subentry_id, runtime in runtime_data.sources.items():
        entities: list[SensorEntity] = []
        if runtime.uses_cumulative_energy or runtime.config.get(
            CHANNEL_CONF_KEYS[CHANNEL_POWER]
        ):
            entities.append(PlnSourceEnergySensor(runtime))
        for channel in MEASUREMENT_CHANNELS:
            if runtime.config.get(CHANNEL_CONF_KEYS[channel]):
                entities.append(PlnSourceMeasurementSensor(runtime, channel))
        if entities:
            async_add_entities(entities, config_subentry_id=subentry_id)


class PlnSourceEnergySensor(PlnSourceEntity, SensorEntity):
    """Energi kumulatif kanonik dalam kWh, aman terhadap reset counter."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2

    def __init__(self, runtime: SourceRuntime) -> None:
        """Siapkan sensor energi."""
        super().__init__(runtime, CHANNEL_ENERGY)

    @property
    def native_value(self) -> float | None:
        """Total kWh menurut akumulator kita sendiri."""
        return self._runtime.energy_kwh

    @property
    def available(self) -> bool:
        """Tersedia selama sumber sehat (atau masih dalam masa tenggang)."""
        return self._runtime.available and self._runtime.energy_kwh is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Atribut untuk audit dan pencocokan dengan angka meter fisik."""
        runtime = self._runtime
        state = runtime.accumulator.state
        attributes = super().extra_state_attributes
        attributes.update(
            {
                ATTR_SOURCE_ENTITY_ID: runtime.config.get(
                    CHANNEL_CONF_KEYS[CHANNEL_ENERGY]
                )
                or runtime.config.get(CHANNEL_CONF_KEYS[CHANNEL_POWER]),
                ATTR_SOURCE_RAW_VALUE: runtime.raw_values.get(CHANNEL_ENERGY),
                ATTR_SOURCE_UNIT: runtime.units.get(CHANNEL_ENERGY),
                ATTR_SOURCE_STATE_CLASS: runtime.state_classes.get(CHANNEL_ENERGY),
                ATTR_UNIT_CONVERSION_FACTOR: runtime.factors.get(CHANNEL_ENERGY),
                ATTR_RESETS_DETECTED: state.resets_detected,
                ATTR_DIPS_DETECTED: state.dips_detected,
                ATTR_LAST_RESET_AT: state.last_reset_at,
                ATTR_LAST_RESET_FROM: state.last_reset_from,
                ATTR_LAST_RESET_TO: state.last_reset_to,
                ATTR_ACCUMULATOR_ZERO_POINT: state.zero_point,
                ATTR_ACCUMULATOR_OFFSET: state.offset,
                ATTR_CONSUMED_SINCE_START: state.consumed,
            }
        )
        return attributes


class PlnSourceMeasurementSensor(PlnSourceEntity, SensorEntity):
    """Pengukuran sesaat: daya, tegangan, arus, frekuensi."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, runtime: SourceRuntime, channel: str) -> None:
        """Siapkan sensor pengukuran untuk satu kanal."""
        super().__init__(runtime, channel)
        config = MEASUREMENT_CHANNELS[channel]
        self._attr_device_class = config["device_class"]
        self._attr_native_unit_of_measurement = config["unit"]
        self._attr_suggested_display_precision = config["precision"]

    @property
    def native_value(self) -> float | None:
        """Nilai terakhir dalam satuan kanonik.

        Saat sumber hilang sebentar, nilai lama sengaja ditahan (bukan dijatuhkan
        ke nol) supaya tidak salah terbaca sebagai "tidak ada beban" - spec K.1.
        """
        return self._runtime.values.get(self._key)

    @property
    def available(self) -> bool:
        """Tersedia selama sumber sehat atau masih dalam masa tenggang."""
        return self._runtime.available and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Atribut asal-usul angka untuk kanal ini."""
        runtime = self._runtime
        attributes = super().extra_state_attributes
        attributes.update(
            {
                ATTR_SOURCE_ENTITY_ID: runtime.config.get(
                    CHANNEL_CONF_KEYS[self._key]
                ),
                ATTR_SOURCE_RAW_VALUE: runtime.raw_values.get(self._key),
                ATTR_SOURCE_UNIT: runtime.units.get(self._key),
                ATTR_UNIT_CONVERSION_FACTOR: runtime.factors.get(self._key),
            }
        )
        return attributes
