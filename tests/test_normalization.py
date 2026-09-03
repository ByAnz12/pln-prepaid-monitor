"""Test lapisan Source Normalization terhadap entity nyata milik user."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from custom_components.pln_prepaid_monitor.const import (
    CHANNEL_CURRENT,
    CHANNEL_ENERGY,
    CHANNEL_FREQUENCY,
    CHANNEL_POWER,
    CHANNEL_VOLTAGE,
    CONF_CURRENT_ENTITY_ID,
    CONF_ENERGY_ENTITY_ID,
    CONF_FREQUENCY_ENTITY_ID,
    CONF_POWER_ENTITY_ID,
    CONF_VOLTAGE_ENTITY_ID,
)
from custom_components.pln_prepaid_monitor.engines.normalization import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    inspect_channel,
    inspect_source,
)

from .conftest import apply_states, BATTERY1, JUWEI, MCB_RUMAH, MCB_TOKO


def _codes(issues) -> set[str]:
    """Kumpulkan kode temuan supaya assertion-nya mudah dibaca."""
    return {issue.code for issue in issues}


async def test_mcb_rumah_power_kw_is_converted_to_watt(hass: HomeAssistant) -> None:
    """MCB RUMAH melaporkan kW; sistem wajib mengonversinya ke W."""
    apply_states(hass, MCB_RUMAH)

    report = inspect_channel(hass, CHANNEL_POWER, "sensor.mcb_rumah_phase_a_power")

    assert report.source_unit == "kW"
    assert report.conversion_factor == pytest.approx(1000.0)
    assert report.normalized_value == pytest.approx(1234.0)
    assert "unit_converted" in _codes(report.issues)
    assert report.usable


async def test_mcb_toko_power_watt_needs_no_conversion(hass: HomeAssistant) -> None:
    """MCB TOKO sudah memakai W; faktornya harus tepat 1 tanpa catatan konversi."""
    apply_states(hass, MCB_TOKO)

    report = inspect_channel(hass, CHANNEL_POWER, "sensor.mcb_toko_phase_a_power")

    assert report.conversion_factor == pytest.approx(1.0)
    assert report.normalized_value == pytest.approx(830.0)
    assert "unit_converted" not in _codes(report.issues)


async def test_battery_energy_without_state_class_is_flagged_not_rejected(
    hass: HomeAssistant,
) -> None:
    """battery1 punya device_class energy tanpa state_class - diberi peringatan."""
    apply_states(hass, BATTERY1)

    report = inspect_channel(
        hass, CHANNEL_ENERGY, "sensor.battery1_total_energy_meter"
    )

    assert report.source_state_class is None
    assert "energy_no_state_class" in _codes(report.issues)
    assert all(issue.severity != SEVERITY_ERROR for issue in report.issues)
    # Tetap bisa dipakai: peringatan, bukan penolakan.
    assert report.usable
    assert report.normalized_value == pytest.approx(812.5)


async def test_unavailable_source_is_warning_only(hass: HomeAssistant) -> None:
    """Sumber yang sedang offline boleh disimpan, hanya diberi peringatan."""
    apply_states(hass, JUWEI)

    report = inspect_channel(
        hass, CHANNEL_POWER, "sensor.ju_wei_dian_neng_biao_cw24_cw20_power"
    )

    assert "entity_unavailable" in _codes(report.issues)
    assert not report.available
    assert report.usable


async def test_missing_entity_is_warning_only(hass: HomeAssistant) -> None:
    """Entity yang belum ada tidak boleh memblokir penyimpanan (spec E)."""
    report = inspect_channel(hass, CHANNEL_ENERGY, "sensor.belum_ada")

    assert "entity_not_found" in _codes(report.issues)
    assert not report.exists
    assert all(issue.severity == SEVERITY_WARNING for issue in report.issues)


async def test_non_numeric_state_is_error(hass: HomeAssistant) -> None:
    """State non-angka harus jadi error, bukan diam-diam dianggap nol."""
    hass.states.async_set(
        "sensor.aneh", "banyak", {"unit_of_measurement": "kWh", "device_class": "energy"}
    )

    report = inspect_channel(hass, CHANNEL_ENERGY, "sensor.aneh")

    assert "state_not_numeric" in _codes(report.issues)
    assert report.has_error
    assert not report.usable


async def test_mcb_rumah_full_source_report(hass: HomeAssistant) -> None:
    """Pemetaan lengkap MCB RUMAH: valid, dengan satu catatan konversi satuan."""
    apply_states(hass, MCB_RUMAH)

    report = inspect_source(
        hass,
        {
            "name": "MCB RUMAH",
            CONF_ENERGY_ENTITY_ID: "sensor.mcb_rumah_total_energy",
            CONF_POWER_ENTITY_ID: "sensor.mcb_rumah_phase_a_power",
            CONF_VOLTAGE_ENTITY_ID: "sensor.mcb_rumah_phase_a_voltage",
            CONF_CURRENT_ENTITY_ID: "sensor.mcb_rumah_phase_a_current",
            CONF_FREQUENCY_ENTITY_ID: "sensor.mcb_rumah_supply_frequency",
        },
    )

    assert report.errors == []
    assert report.energy_source_of_truth == "cumulative"
    assert report.channels[CHANNEL_ENERGY].normalized_value == pytest.approx(15498.27)
    assert report.channels[CHANNEL_POWER].normalized_value == pytest.approx(1234.0)
    assert report.channels[CHANNEL_VOLTAGE].normalized_value == pytest.approx(221.4)
    assert report.channels[CHANNEL_CURRENT].normalized_value == pytest.approx(5.6)
    assert report.channels[CHANNEL_FREQUENCY].normalized_value == pytest.approx(50.0)


async def test_source_without_energy_or_power_is_rejected(
    hass: HomeAssistant,
) -> None:
    """Tanpa kWh maupun daya, tidak ada yang bisa dihitung."""
    apply_states(hass, MCB_RUMAH)

    report = inspect_source(
        hass,
        {
            "name": "Kosong",
            CONF_VOLTAGE_ENTITY_ID: "sensor.mcb_rumah_phase_a_voltage",
        },
    )

    assert "no_measurement_entity" in _codes(report.errors)


async def test_power_only_source_falls_back_to_integration(
    hass: HomeAssistant,
) -> None:
    """Hanya daya: sah, tapi ditandai sebagai estimasi."""
    apply_states(hass, MCB_TOKO)

    report = inspect_source(
        hass,
        {
            "name": "Hanya daya",
            CONF_POWER_ENTITY_ID: "sensor.mcb_toko_phase_a_power",
        },
    )

    assert report.errors == []
    assert "energy_from_power" in _codes(report.all_issues)
    assert report.energy_source_of_truth == "integrated_from_power"


async def test_empty_name_is_rejected(hass: HomeAssistant) -> None:
    """Nama kosong ditolak, bukan diam-diam diisi sendiri."""
    apply_states(hass, MCB_RUMAH)

    report = inspect_source(
        hass,
        {"name": "   ", CONF_ENERGY_ENTITY_ID: "sensor.mcb_rumah_total_energy"},
    )

    assert "name_required" in _codes(report.errors)
