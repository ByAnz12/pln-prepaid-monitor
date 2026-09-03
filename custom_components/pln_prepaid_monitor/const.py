"""Konstanta untuk integrasi PLN Prepaid Energy & Cost Monitor.

Semua nilai di sini adalah *kunci konfigurasi* dan *nilai default awal*.
Tidak ada tarif, threshold, atau periode yang di-hardcode sebagai logika:
apa pun yang bersifat kebijakan wajib bisa diubah user lewat UI.
"""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "pln_prepaid_monitor"

# Integrasi ini HANYA membaca. Daftar platform di bawah sengaja tidak pernah
# memuat switch/number/select/button: sistem ini tidak boleh punya kemampuan
# memutus atau menyalakan listrik. Lihat tests/test_readonly_guarantee.py.
PLATFORMS: Final[list[Platform]] = [Platform.SENSOR, Platform.BINARY_SENSOR]

# Domain entity yang boleh dipilih user sebagai sumber data.
ALLOWED_SOURCE_DOMAINS: Final = ("sensor",)
ALLOWED_AVAILABILITY_DOMAINS: Final = ("binary_sensor", "sensor")

SUBENTRY_TYPE_ENERGY_SOURCE: Final = "energy_source"

# --- kunci konfigurasi Energy Source -----------------------------------------
CONF_DEVICE_ID: Final = "device_id"
CONF_SHOW_ALL_SENSORS: Final = "show_all_sensors"
CONF_ENERGY_ENTITY_ID: Final = "energy_entity_id"
CONF_POWER_ENTITY_ID: Final = "power_entity_id"
CONF_VOLTAGE_ENTITY_ID: Final = "voltage_entity_id"
CONF_CURRENT_ENTITY_ID: Final = "current_entity_id"
CONF_FREQUENCY_ENTITY_ID: Final = "frequency_entity_id"
CONF_AVAILABILITY_ENTITY_ID: Final = "availability_entity_id"
CONF_UNAVAILABLE_GRACE_MINUTES: Final = "unavailable_grace_minutes"
CONF_ENABLED: Final = "enabled"

DEFAULT_UNAVAILABLE_GRACE_MINUTES: Final = 5

# --- peran kanal (channel) kanonik -------------------------------------------
CHANNEL_ENERGY: Final = "energy"
CHANNEL_POWER: Final = "power"
CHANNEL_VOLTAGE: Final = "voltage"
CHANNEL_CURRENT: Final = "current"
CHANNEL_FREQUENCY: Final = "frequency"

CHANNEL_CONF_KEYS: Final = {
    CHANNEL_ENERGY: CONF_ENERGY_ENTITY_ID,
    CHANNEL_POWER: CONF_POWER_ENTITY_ID,
    CHANNEL_VOLTAGE: CONF_VOLTAGE_ENTITY_ID,
    CHANNEL_CURRENT: CONF_CURRENT_ENTITY_ID,
    CHANNEL_FREQUENCY: CONF_FREQUENCY_ENTITY_ID,
}

# --- asal-usul angka energi (dipublikasikan sebagai atribut entity) ----------
SOURCE_OF_TRUTH_CUMULATIVE: Final = "cumulative"
SOURCE_OF_TRUTH_INTEGRATED: Final = "integrated_from_power"

# --- penyimpanan state akumulator lintas restart -----------------------------
STORAGE_VERSION: Final = 1
STORAGE_KEY: Final = f"{DOMAIN}.runtime"
STORAGE_SAVE_DELAY_SECONDS: Final = 30

# --- atribut entity ----------------------------------------------------------
ATTR_SOURCE_ENTITY_ID: Final = "source_entity_id"
ATTR_SOURCE_RAW_VALUE: Final = "source_raw_value"
ATTR_SOURCE_UNIT: Final = "source_unit"
ATTR_SOURCE_STATE_CLASS: Final = "source_state_class"
ATTR_SOURCE_OF_TRUTH: Final = "source_of_truth"
ATTR_UNIT_CONVERSION_FACTOR: Final = "unit_conversion_factor"
ATTR_RESETS_DETECTED: Final = "resets_detected"
ATTR_DIPS_DETECTED: Final = "dips_detected"
ATTR_ACCUMULATOR_ZERO_POINT: Final = "accumulator_zero_point"
ATTR_ACCUMULATOR_OFFSET: Final = "accumulator_offset"
ATTR_CONSUMED_SINCE_START: Final = "consumed_since_start_kwh"
ATTR_HOLDING_LAST_VALUE: Final = "holding_last_value"
ATTR_UNAVAILABLE_SINCE: Final = "unavailable_since"
