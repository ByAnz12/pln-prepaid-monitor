"""Test alur konfigurasi tarif dan pemilihannya di kelompok tagihan."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pln_prepaid_monitor.const import (
    CONF_CYCLE_PERIODS,
    CONF_DAY_START_TIME,
    CONF_ENERGY_ENTITY_ID,
    CONF_FIXED_CHARGE_PERIOD,
    CONF_FIXED_CHARGE_RP,
    CONF_MONTH_START_DAY,
    CONF_RATE_HISTORY,
    CONF_RATE_RP_PER_KWH,
    CONF_RESET_HOLD_THRESHOLD_KWH,
    CONF_ROUNDING_MODE,
    CONF_ROUNDING_UNIT_RP,
    CONF_SOURCE_IDS,
    CONF_TARIFF_ID,
    CONF_TOKEN_ENABLED,
    CONF_WEEK_START_DAY,
    CONF_YEAR_START_MONTH,
    DEFAULT_RATE_RP_PER_KWH,
    DOMAIN,
    SUBENTRY_TYPE_BILLING_GROUP,
    SUBENTRY_TYPE_ENERGY_SOURCE,
    SUBENTRY_TYPE_TARIFF,
)

from .conftest import apply_states, MCB_RUMAH

RUMAH_ID = "src_rumah"
TARIFF_ID = "tar_r1"

TARIFF_INPUT = {
    "name": "Tarif R-1",
    CONF_RATE_RP_PER_KWH: 1444.70,
    CONF_FIXED_CHARGE_RP: 0.0,
    CONF_FIXED_CHARGE_PERIOD: "monthly",
    CONF_ROUNDING_MODE: "nearest",
    CONF_ROUNDING_UNIT_RP: 1.0,
}

TOKEN_INPUT = {
    CONF_TOKEN_ENABLED: False,
    CONF_RESET_HOLD_THRESHOLD_KWH: 1.0,
}

CYCLES_INPUT = {
    CONF_CYCLE_PERIODS: ["day", "month"],
    CONF_DAY_START_TIME: "00:00:00",
    CONF_WEEK_START_DAY: "monday",
    CONF_MONTH_START_DAY: 1,
    CONF_YEAR_START_MONTH: "january",
}

SOURCE_SUBENTRY = {
    "data": {
        "name": "MCB RUMAH",
        CONF_ENERGY_ENTITY_ID: "sensor.mcb_rumah_total_energy",
    },
    "subentry_id": RUMAH_ID,
    "subentry_type": SUBENTRY_TYPE_ENERGY_SOURCE,
    "title": "MCB RUMAH",
    "unique_id": None,
}

TARIFF_SUBENTRY = {
    "data": {**TARIFF_INPUT, CONF_RATE_HISTORY: []},
    "subentry_id": TARIFF_ID,
    "subentry_type": SUBENTRY_TYPE_TARIFF,
    "title": "Tarif R-1",
    "unique_id": None,
}


async def _setup(hass: HomeAssistant, *subentries) -> MockConfigEntry:
    """Pasang integrasi dengan subentry yang diberikan."""
    await hass.config.async_set_time_zone("Asia/Jakarta")
    entry = MockConfigEntry(domain=DOMAIN, data={}, subentries_data=list(subentries))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_create_tariff(hass: HomeAssistant) -> None:
    """Tarif baru dibuat lewat satu form."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_TARIFF), context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "tariff"
    # Angka bawaan disebutkan di teks form supaya user tahu itu hanya perkiraan
    # dan harus disesuaikan dengan golongan dayanya sendiri.
    assert result["description_placeholders"]["default_rate"].startswith("1.444")

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], dict(TARIFF_INPUT)
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    tariff = next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_TARIFF
    )
    assert tariff.data[CONF_RATE_RP_PER_KWH] == pytest.approx(1444.70)
    # Versi pertama langsung tercatat di riwayat.
    assert len(tariff.data[CONF_RATE_HISTORY]) == 1


async def test_default_rate_matches_spec(hass: HomeAssistant) -> None:
    """Nilai bawaan form adalah angka indikatif dari spec, bukan angka lain."""
    assert DEFAULT_RATE_RP_PER_KWH == pytest.approx(1444.70)


async def test_rate_must_be_positive(hass: HomeAssistant) -> None:
    """Tarif nol atau negatif ditolak, bukan diam-diam diterima."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_TARIFF), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**TARIFF_INPUT, CONF_RATE_RP_PER_KWH: 0}
    )

    assert result["step_id"] == "tariff"
    assert result["errors"] == {"base": "rate_must_be_positive"}


async def test_duplicate_tariff_name_is_rejected(hass: HomeAssistant) -> None:
    """Dua tarif tidak boleh bernama sama."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_TARIFF), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], dict(TARIFF_INPUT)
    )

    assert result["step_id"] == "tariff"
    assert result["errors"] == {"base": "name_duplicate"}


async def test_editing_rate_appends_history_version(hass: HomeAssistant) -> None:
    """Mengubah tarif menyimpan versi baru, tidak menimpa yang lama (spec K.7)."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_TARIFF),
        context={"source": "reconfigure", "subentry_id": TARIFF_ID},
    )
    assert result["step_id"] == "tariff"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**TARIFF_INPUT, CONF_RATE_RP_PER_KWH: 1600.0}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    history = entry.subentries[TARIFF_ID].data[CONF_RATE_HISTORY]
    assert len(history) == 1
    assert history[0]["rate_rp_per_kwh"] == pytest.approx(1600.0)
    assert history[0]["effective_from"]


async def test_billing_group_can_pick_a_tariff(hass: HomeAssistant) -> None:
    """Langkah tarif muncul di alur kelompok tagihan dan tersimpan."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_BILLING_GROUP),
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "PLN RUMAH", CONF_SOURCE_IDS: [RUMAH_ID]}
    )
    assert result["step_id"] == "tariff"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_TARIFF_ID: TARIFF_ID}
    )
    assert result["step_id"] == "token"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], dict(TOKEN_INPUT)
    )
    assert result["step_id"] == "cycles"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], dict(CYCLES_INPUT)
    )
    assert result["step_id"] == "review"
    summary = result["description_placeholders"]["summary"]
    assert "Tarif R-1" in summary
    assert "1.444" in summary

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    group = next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_BILLING_GROUP
    )
    assert group.data[CONF_TARIFF_ID] == TARIFF_ID


async def test_tariff_step_is_skipped_when_none_exist(hass: HomeAssistant) -> None:
    """Belum ada tarif: langkahnya dilewati, kelompok tetap bisa dibuat."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_BILLING_GROUP),
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "PLN RUMAH", CONF_SOURCE_IDS: [RUMAH_ID]}
    )

    # Langsung ke langkah token, tanpa langkah tarif.
    assert result["step_id"] == "token"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], dict(TOKEN_INPUT)
    )
    assert result["step_id"] == "cycles"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], dict(CYCLES_INPUT)
    )
    assert result["step_id"] == "review"
    # Ringkasan menyebutkan bahwa biaya belum akan dihitung.
    assert "belum" in result["description_placeholders"]["summary"].lower() or (
        "not selected" in result["description_placeholders"]["summary"].lower()
    )

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_tariff_can_be_left_empty(hass: HomeAssistant) -> None:
    """User boleh melewati pilihan tarif walaupun tarifnya sudah ada."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_BILLING_GROUP),
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "PLN RUMAH", CONF_SOURCE_IDS: [RUMAH_ID]}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {}
    )
    assert result["step_id"] == "token"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], dict(TOKEN_INPUT)
    )
    assert result["step_id"] == "cycles"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], dict(CYCLES_INPUT)
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    group = next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_BILLING_GROUP
    )
    assert group.data.get(CONF_TARIFF_ID) is None
