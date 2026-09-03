"""Test alur konfigurasi Billing Group."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pln_prepaid_monitor.const import (
    CONF_CYCLE_PERIODS,
    CONF_DAY_START_TIME,
    CONF_ENERGY_ENTITY_ID,
    CONF_MONTH_START_DAY,
    CONF_POWER_ENTITY_ID,
    CONF_SOURCE_IDS,
    CONF_WEEK_START_DAY,
    CONF_YEAR_START_MONTH,
    DOMAIN,
    SUBENTRY_TYPE_BILLING_GROUP,
    SUBENTRY_TYPE_ENERGY_SOURCE,
)

from .conftest import apply_states, MCB_RUMAH, MCB_TOKO

RUMAH_ID = "src_rumah"
TOKO_ID = "src_toko"

CYCLES_INPUT = {
    CONF_CYCLE_PERIODS: ["day", "month"],
    CONF_DAY_START_TIME: "00:00:00",
    CONF_WEEK_START_DAY: "monday",
    CONF_MONTH_START_DAY: 1,
    CONF_YEAR_START_MONTH: "january",
}


def _source(subentry_id: str, name: str, prefix: str) -> dict:
    """Subentry Energy Source siap pakai."""
    return {
        "data": {
            "name": name,
            CONF_ENERGY_ENTITY_ID: f"sensor.{prefix}_total_energy",
            CONF_POWER_ENTITY_ID: f"sensor.{prefix}_phase_a_power",
        },
        "subentry_id": subentry_id,
        "subentry_type": SUBENTRY_TYPE_ENERGY_SOURCE,
        "title": name,
        "unique_id": None,
    }


async def _entry_with_sources(hass: HomeAssistant, *extra) -> MockConfigEntry:
    """Config entry berisi MCB RUMAH dan MCB TOKO."""
    await hass.config.async_set_time_zone("Asia/Jakarta")
    subentries = [
        _source(RUMAH_ID, "MCB RUMAH", "mcb_rumah"),
        _source(TOKO_ID, "MCB TOKO", "mcb_toko"),
        *extra,
    ]
    entry = MockConfigEntry(domain=DOMAIN, data={}, subentries_data=subentries)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _start_flow(hass: HomeAssistant, entry: MockConfigEntry):
    """Mulai flow tambah Billing Group."""
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_BILLING_GROUP),
        context={"source": SOURCE_USER},
    )


async def test_create_billing_group(hass: HomeAssistant) -> None:
    """PLN RUMAH dibuat dari MCB RUMAH lewat tiga langkah."""
    apply_states(hass, MCB_RUMAH, MCB_TOKO)
    entry = await _entry_with_sources(hass)

    result = await _start_flow(hass, entry)
    assert result["step_id"] == "members"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "PLN RUMAH", CONF_SOURCE_IDS: [RUMAH_ID]}
    )
    assert result["step_id"] == "cycles"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], dict(CYCLES_INPUT)
    )
    assert result["step_id"] == "review"

    summary = result["description_placeholders"]["summary"]
    assert "PLN RUMAH" in summary
    assert "MCB RUMAH" in summary
    # Kapan tiap penghitung akan di-reset harus terlihat sebelum disimpan.
    assert "reset" in summary.lower()

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    groups = [
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_BILLING_GROUP
    ]
    assert len(groups) == 1
    assert groups[0].data[CONF_SOURCE_IDS] == [RUMAH_ID]
    assert groups[0].data[CONF_CYCLE_PERIODS] == ["day", "month"]


async def test_aborts_when_no_energy_source_exists(hass: HomeAssistant) -> None:
    """Tidak masuk akal membuat kelompok tagihan tanpa sumber energi."""
    await hass.config.async_set_time_zone("Asia/Jakarta")
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await _start_flow(hass, entry)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_energy_sources"


async def test_requires_at_least_one_source(hass: HomeAssistant) -> None:
    """Tanpa memilih sumber, form menolak dengan pesan yang jelas."""
    apply_states(hass, MCB_RUMAH, MCB_TOKO)
    entry = await _entry_with_sources(hass)

    result = await _start_flow(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "PLN RUMAH", CONF_SOURCE_IDS: []}
    )

    assert result["step_id"] == "members"
    assert result["errors"] == {"base": "no_sources_selected"}


async def test_requires_at_least_one_period(hass: HomeAssistant) -> None:
    """Tanpa periode, tidak ada penghitung yang bisa dibuat."""
    apply_states(hass, MCB_RUMAH, MCB_TOKO)
    entry = await _entry_with_sources(hass)

    result = await _start_flow(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "PLN RUMAH", CONF_SOURCE_IDS: [RUMAH_ID]}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], dict(CYCLES_INPUT) | {CONF_CYCLE_PERIODS: []}
    )

    assert result["step_id"] == "cycles"
    assert result["errors"] == {"base": "no_periods_selected"}


async def test_duplicate_group_name_is_rejected(hass: HomeAssistant) -> None:
    """Dua kelompok tagihan tidak boleh bernama sama."""
    apply_states(hass, MCB_RUMAH, MCB_TOKO)
    entry = await _entry_with_sources(
        hass,
        {
            "data": {
                "name": "PLN RUMAH",
                CONF_SOURCE_IDS: [RUMAH_ID],
                CONF_CYCLE_PERIODS: ["day"],
            },
            "subentry_id": "grp_rumah",
            "subentry_type": SUBENTRY_TYPE_BILLING_GROUP,
            "title": "PLN RUMAH",
            "unique_id": None,
        },
    )

    result = await _start_flow(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "PLN RUMAH", CONF_SOURCE_IDS: [TOKO_ID]}
    )

    assert result["step_id"] == "members"
    assert result["errors"] == {"base": "name_duplicate"}


async def test_overlapping_source_gives_warning_not_block(
    hass: HomeAssistant,
) -> None:
    """Sumber yang dipakai dua kelompok diperingatkan, tapi tetap boleh (K.11)."""
    apply_states(hass, MCB_RUMAH, MCB_TOKO)
    entry = await _entry_with_sources(
        hass,
        {
            "data": {
                "name": "PLN RUMAH",
                CONF_SOURCE_IDS: [RUMAH_ID],
                CONF_CYCLE_PERIODS: ["day"],
            },
            "subentry_id": "grp_rumah",
            "subentry_type": SUBENTRY_TYPE_BILLING_GROUP,
            "title": "PLN RUMAH",
            "unique_id": None,
        },
    )

    result = await _start_flow(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "PLN SEMUA", CONF_SOURCE_IDS: [RUMAH_ID, TOKO_ID]},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], dict(CYCLES_INPUT)
    )

    assert result["step_id"] == "review"
    summary = result["description_placeholders"]["summary"]
    assert "⚠️" in summary
    assert "PLN RUMAH" in summary
    assert "MCB RUMAH" in summary

    # Peringatan, bukan penolakan: user tetap boleh menyimpan kalau memang sengaja.
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_reconfigure_billing_group(hass: HomeAssistant) -> None:
    """Mengubah anggota dan periode kelompok yang sudah ada."""
    apply_states(hass, MCB_RUMAH, MCB_TOKO)
    entry = await _entry_with_sources(
        hass,
        {
            "data": {
                "name": "PLN RUMAH",
                CONF_SOURCE_IDS: [RUMAH_ID],
                CONF_CYCLE_PERIODS: ["day"],
            },
            "subentry_id": "grp_rumah",
            "subentry_type": SUBENTRY_TYPE_BILLING_GROUP,
            "title": "PLN RUMAH",
            "unique_id": None,
        },
    )

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_BILLING_GROUP),
        context={"source": "reconfigure", "subentry_id": "grp_rumah"},
    )
    assert result["step_id"] == "members"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "PLN RUMAH", CONF_SOURCE_IDS: [RUMAH_ID, TOKO_ID]},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], dict(CYCLES_INPUT) | {CONF_CYCLE_PERIODS: ["day", "year"]}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    updated = entry.subentries["grp_rumah"]
    assert updated.data[CONF_SOURCE_IDS] == [RUMAH_ID, TOKO_ID]
    assert updated.data[CONF_CYCLE_PERIODS] == ["day", "year"]


async def test_custom_cycle_boundaries_are_saved(hass: HomeAssistant) -> None:
    """Jam mulai hari dan tanggal mulai bulan benar-benar tersimpan."""
    apply_states(hass, MCB_RUMAH, MCB_TOKO)
    entry = await _entry_with_sources(hass)

    result = await _start_flow(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "PLN TOKO", CONF_SOURCE_IDS: [TOKO_ID]}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_CYCLE_PERIODS: ["day", "month"],
            CONF_DAY_START_TIME: "06:00:00",
            CONF_WEEK_START_DAY: "sunday",
            CONF_MONTH_START_DAY: 15,
            CONF_YEAR_START_MONTH: "july",
        },
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
    assert group.data[CONF_DAY_START_TIME] == "06:00:00"
    assert group.data[CONF_WEEK_START_DAY] == "sunday"
    assert group.data[CONF_MONTH_START_DAY] == 15
    assert group.data[CONF_YEAR_START_MONTH] == "july"
