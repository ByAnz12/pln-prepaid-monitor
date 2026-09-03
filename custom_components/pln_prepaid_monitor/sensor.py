"""Entity sensor untuk Energy Source dan Billing Group.

Per **Energy Source** (sampai lima sensor, satuannya sudah diseragamkan apa pun
satuan aslinya di perangkat):

* energi (kWh) - selalu dibuat selama ada sensor kWh atau sensor daya
* daya (W), tegangan (V), arus (A), frekuensi (Hz) - dibuat bila dipetakan

Per **Billing Group**:

* total energi gabungan (kWh) dan daya gabungan (W)
* penghitung pemakaian per periode: jam ini, hari ini, minggu ini, bulan ini,
  tahun ini - periode mana saja yang dibuat mengikuti pilihan user
"""

from __future__ import annotations

from datetime import datetime
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
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_ACCUMULATOR_OFFSET,
    ATTR_ACCUMULATOR_ZERO_POINT,
    ATTR_ACTIVE_RATE,
    ATTR_CONSUMED_SINCE_START,
    ATTR_CYCLE_START,
    ATTR_DIPS_DETECTED,
    ATTR_ENERGY_COST_ONLY,
    ATTR_FIXED_CHARGE_INCLUDED,
    ATTR_LAST_RESET_AT,
    ATTR_LAST_RESET_FROM,
    ATTR_LAST_RESET_TO,
    ATTR_NEXT_CYCLE_START,
    ATTR_RATE_HISTORY,
    ATTR_RESETS_DETECTED,
    ATTR_SOURCE_ENTITY_ID,
    ATTR_SOURCE_RAW_VALUE,
    ATTR_SOURCE_STATE_CLASS,
    ATTR_SOURCE_UNIT,
    ATTR_TARIFF_NAME,
    ATTR_UNIT_CONVERSION_FACTOR,
    CHANNEL_CONF_KEYS,
    CHANNEL_CURRENT,
    CHANNEL_ENERGY,
    CHANNEL_FREQUENCY,
    CHANNEL_POWER,
    CHANNEL_VOLTAGE,
    DEFAULT_CURRENCY,
)
from .coordinator import BillingGroupRuntime, PlnRuntimeData, SourceRuntime
from .engines.cost_engine import apply_rounding
from .engines.period import next_cycle_start
from .entity import PlnBillingGroupEntity, PlnSourceEntity

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

    currency = hass.config.currency or DEFAULT_CURRENCY

    for subentry_id, group in runtime_data.billing_groups.items():
        group_entities: list[SensorEntity] = [
            PlnGroupEnergyTotalSensor(group),
            PlnGroupPowerSensor(group),
        ]
        group_entities.extend(
            PlnGroupPeriodEnergySensor(group, period) for period in group.periods
        )
        # Sensor biaya hanya dibuat kalau kelompok ini sudah punya tarif.
        if group.has_cost:
            group_entities.append(PlnGroupCostTotalSensor(group, currency))
            group_entities.extend(
                PlnGroupPeriodCostSensor(group, period, currency)
                for period in group.periods
            )
        async_add_entities(group_entities, config_subentry_id=subentry_id)


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


class PlnGroupEnergyTotalSensor(PlnBillingGroupEntity, SensorEntity):
    """Total energi gabungan satu Billing Group, dalam kWh."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2

    def __init__(self, group: BillingGroupRuntime) -> None:
        """Siapkan sensor total energi grup."""
        super().__init__(group, "energy_total")

    @property
    def native_value(self) -> float | None:
        """Total kWh seluruh anggota grup."""
        return self._group.total_kwh

    @property
    def available(self) -> bool:
        """Tersedia begitu ada minimal satu anggota yang mengirim data."""
        return self._group.total_kwh is not None


class PlnGroupPowerSensor(PlnBillingGroupEntity, SensorEntity):
    """Daya gabungan satu Billing Group, dalam Watt."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 1

    def __init__(self, group: BillingGroupRuntime) -> None:
        """Siapkan sensor daya grup."""
        super().__init__(group, "power")

    @property
    def native_value(self) -> float | None:
        """Jumlah daya seluruh anggota grup."""
        return self._group.power_w

    @property
    def available(self) -> bool:
        """Tersedia selama ada minimal satu anggota yang melaporkan daya."""
        return self._group.power_w is not None


class PlnGroupPeriodEnergySensor(PlnBillingGroupEntity, SensorEntity):
    """Pemakaian pada periode berjalan: jam ini, hari ini, dan seterusnya."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3

    def __init__(self, group: BillingGroupRuntime, period: str) -> None:
        """Siapkan penghitung untuk satu periode."""
        super().__init__(group, f"energy_this_{period}")
        self._period = period

    @property
    def native_value(self) -> float | None:
        """Pemakaian sejak awal siklus berjalan."""
        return self._group.period_value(self._period)

    @property
    def last_reset(self) -> datetime | None:
        """Kapan penghitung ini terakhir dimulai dari nol.

        Home Assistant memakai nilai ini untuk mengerti batas siklus pada sensor
        ber-``state_class: total``, sehingga statistik jangka panjangnya benar.
        """
        return self._group.period_cycle_start(self._period)

    @property
    def available(self) -> bool:
        """Tersedia begitu penghitungnya punya titik awal."""
        return self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Tambahkan kapan siklus ini dimulai dan kapan akan di-reset."""
        attributes = super().extra_state_attributes
        cycle_start_at = self._group.period_cycle_start(self._period)
        attributes[ATTR_CYCLE_START] = (
            cycle_start_at.isoformat() if cycle_start_at else None
        )
        attributes[ATTR_NEXT_CYCLE_START] = next_cycle_start(
            self._period, dt_util.now(), self._group.cycle_config
        ).isoformat()
        return attributes


class PlnGroupCostTotalSensor(PlnBillingGroupEntity, SensorEntity):
    """Total biaya berjalan satu Billing Group, dalam Rupiah.

    Hanya berisi biaya dari energi yang benar-benar dipakai. Biaya beban tidak
    dicampur ke sini - lihat sensor biaya bulanan.
    """

    _attr_device_class = SensorDeviceClass.MONETARY
    # Home Assistant Core 2026.8.3 hanya mengizinkan `total` untuk device_class
    # monetary (DEVICE_CLASS_STATE_CLASSES di components/sensor/const.py).
    # Memakai `total_increasing` akan memicu peringatan di log.
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 0

    def __init__(self, group: BillingGroupRuntime, currency: str) -> None:
        """Siapkan sensor total biaya."""
        super().__init__(group, "cost_total")
        self._attr_native_unit_of_measurement = currency

    @property
    def native_value(self) -> float | None:
        """Total Rupiah, dibulatkan sesuai pengaturan tarif."""
        return apply_rounding(self._group.cost_total_rp, self._group.tariff)

    @property
    def available(self) -> bool:
        """Tersedia begitu ada pemakaian yang bisa dihitung biayanya."""
        return self._group.cost_total_rp is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Tarif aktif dan riwayat perubahannya, untuk audit."""
        attributes = super().extra_state_attributes
        attributes.update(
            {
                ATTR_TARIFF_NAME: self._group.tariff_name,
                ATTR_ACTIVE_RATE: self._group.active_rate,
                ATTR_RATE_HISTORY: self._group.rate_history,
            }
        )
        return attributes


class PlnGroupPeriodCostSensor(PlnBillingGroupEntity, SensorEntity):
    """Biaya pada periode berjalan: jam ini, hari ini, dan seterusnya."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 0

    def __init__(
        self, group: BillingGroupRuntime, period: str, currency: str
    ) -> None:
        """Siapkan sensor biaya untuk satu periode."""
        super().__init__(group, f"cost_this_{period}")
        self._period = period
        self._attr_native_unit_of_measurement = currency

    @property
    def native_value(self) -> float | None:
        """Biaya periode berjalan, sudah termasuk biaya beban bila berlaku."""
        return apply_rounding(
            self._group.cost_period_total(self._period), self._group.tariff
        )

    @property
    def last_reset(self) -> datetime | None:
        """Kapan penghitung biaya ini terakhir dimulai dari nol."""
        return self._group.period_cycle_start(self._period)

    @property
    def available(self) -> bool:
        """Tersedia begitu penghitung biayanya punya titik awal."""
        return self._group.cost_period_total(self._period) is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Pisahkan biaya energi dari biaya beban, supaya angkanya jelas asalnya."""
        attributes = super().extra_state_attributes
        cycle_start_at = self._group.period_cycle_start(self._period)
        attributes[ATTR_CYCLE_START] = (
            cycle_start_at.isoformat() if cycle_start_at else None
        )
        attributes[ATTR_NEXT_CYCLE_START] = next_cycle_start(
            self._period, dt_util.now(), self._group.cycle_config
        ).isoformat()
        attributes[ATTR_ENERGY_COST_ONLY] = self._group.cost_period_value(
            self._period
        )
        attributes[ATTR_FIXED_CHARGE_INCLUDED] = (
            self._group.cost_period_fixed_charge(self._period)
        )
        attributes[ATTR_ACTIVE_RATE] = self._group.active_rate
        return attributes
