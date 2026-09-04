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
SUBENTRY_TYPE_BILLING_GROUP: Final = "billing_group"
SUBENTRY_TYPE_TARIFF: Final = "tariff"

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

# --- kunci konfigurasi Billing Group -----------------------------------------
CONF_SOURCE_IDS: Final = "source_ids"
CONF_CYCLE_PERIODS: Final = "cycle_periods"
CONF_DAY_START_TIME: Final = "day_start_time"
CONF_WEEK_START_DAY: Final = "week_start_day"
CONF_MONTH_START_DAY: Final = "month_start_day"
CONF_YEAR_START_MONTH: Final = "year_start_month"

DEFAULT_CYCLE_PERIODS: Final = ("hour", "day", "week", "month", "year")
DEFAULT_DAY_START_TIME: Final = "00:00:00"
DEFAULT_WEEK_START_DAY: Final = "monday"
DEFAULT_MONTH_START_DAY: Final = 1
DEFAULT_YEAR_START_MONTH: Final = "january"

# --- kunci konfigurasi Tarif --------------------------------------------------
CONF_TARIFF_ID: Final = "tariff_id"
CONF_RATE_RP_PER_KWH: Final = "rate_rp_per_kwh"
CONF_FIXED_CHARGE_RP: Final = "fixed_charge_rp"
CONF_FIXED_CHARGE_PERIOD: Final = "fixed_charge_period"
CONF_ROUNDING_MODE: Final = "rounding_mode"
CONF_ROUNDING_UNIT_RP: Final = "rounding_unit_rp"
CONF_RATE_HISTORY: Final = "rate_history"

# Tarif awal yang ditampilkan di form. INDIKATIF, bukan angka resmi: perkiraan
# golongan R-1 1300-2200VA dari agregator berita tarif, bukan kutipan langsung
# pln.co.id (spec B.2, confidence LIKELY). Help text di form wajib menyebutkan
# bahwa user harus menyesuaikannya dengan golongan daya dan wilayahnya.
DEFAULT_RATE_RP_PER_KWH: Final = 1444.70
DEFAULT_FIXED_CHARGE_RP: Final = 0.0
DEFAULT_FIXED_CHARGE_PERIOD: Final = "monthly"
DEFAULT_ROUNDING_MODE: Final = "nearest"
DEFAULT_ROUNDING_UNIT_RP: Final = 1.0
DEFAULT_CURRENCY: Final = "IDR"

# --- kunci konfigurasi Token --------------------------------------------------
CONF_TOKEN_ENABLED: Final = "token_enabled"
CONF_RESET_HOLD_THRESHOLD_KWH: Final = "reset_hold_threshold_kwh"

DEFAULT_TOKEN_ENABLED: Final = False
DEFAULT_RESET_HOLD_THRESHOLD_KWH: Final = 1.0

# --- nama service --------------------------------------------------------------
SERVICE_ADD_TOKEN_TOPUP: Final = "add_token_topup"
SERVICE_CALIBRATE_TOKEN_READING: Final = "calibrate_token_reading"
SERVICE_EDIT_TOPUP: Final = "edit_topup"
SERVICE_DELETE_TOPUP: Final = "delete_topup"
SERVICE_RESET_TOKEN_LEDGER: Final = "reset_token_ledger"
SERVICE_RESOLVE_LEDGER_HOLD: Final = "resolve_ledger_hold"

ATTR_KWH_CREDITED: Final = "kwh_credited"
ATTR_NOMINAL_RP: Final = "nominal_rp"
ATTR_TIMESTAMP: Final = "timestamp"
ATTR_METER_READING_BEFORE: Final = "meter_reading_before"
ATTR_METER_READING_AFTER: Final = "meter_reading_after"
ATTR_NOTE: Final = "note"
ATTR_TOPUP_ID: Final = "topup_id"
ATTR_ACTUAL_REMAINING_KWH: Final = "actual_remaining_kwh"
ATTR_HOLD_ACTION: Final = "action"

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
ATTR_LAST_RESET_AT: Final = "last_reset_detected_at"
ATTR_LAST_RESET_FROM: Final = "last_reset_from_kwh"
ATTR_LAST_RESET_TO: Final = "last_reset_to_kwh"
ATTR_ACCUMULATOR_ZERO_POINT: Final = "accumulator_zero_point"
ATTR_ACCUMULATOR_OFFSET: Final = "accumulator_offset"
ATTR_CONSUMED_SINCE_START: Final = "consumed_since_start_kwh"
ATTR_HOLDING_LAST_VALUE: Final = "holding_last_value"
ATTR_UNAVAILABLE_SINCE: Final = "unavailable_since"
ATTR_MEMBER_SOURCES: Final = "member_sources"
ATTR_MEMBERS_UNAVAILABLE: Final = "members_unavailable"
ATTR_CYCLE_START: Final = "cycle_start"
ATTR_NEXT_CYCLE_START: Final = "next_cycle_start"
ATTR_TARIFF_NAME: Final = "tariff_name"
ATTR_ACTIVE_RATE: Final = "active_rate_rp_per_kwh"
ATTR_RATE_HISTORY: Final = "rate_history"
ATTR_FIXED_CHARGE_INCLUDED: Final = "fixed_charge_included_rp"
ATTR_ENERGY_COST_ONLY: Final = "energy_cost_only_rp"
ATTR_TOTAL_CREDITED: Final = "total_credited_kwh"
ATTR_TOPUP_COUNT: Final = "topup_count"
ATTR_LAST_TOPUP_AT: Final = "last_topup_at"
ATTR_TOPUP_HISTORY: Final = "topup_history"
ATTR_LEDGER_ON_HOLD: Final = "ledger_on_hold"
ATTR_HOLD_SINCE: Final = "hold_since"
ATTR_HOLD_SOURCE: Final = "hold_source"
ATTR_HOLD_RESET_FROM: Final = "hold_reset_from_kwh"
ATTR_HOLD_RESET_TO: Final = "hold_reset_to_kwh"
