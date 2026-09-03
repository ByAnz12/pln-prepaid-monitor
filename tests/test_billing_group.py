"""Test entity Billing Group: total gabungan dan penghitung per periode."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.pln_prepaid_monitor.const import (
    ATTR_MEMBER_SOURCES,
    ATTR_MEMBERS_UNAVAILABLE,
    ATTR_NEXT_CYCLE_START,
    CONF_CYCLE_PERIODS,
    CONF_ENERGY_ENTITY_ID,
    CONF_POWER_ENTITY_ID,
    CONF_SOURCE_IDS,
    DOMAIN,
    SUBENTRY_TYPE_BILLING_GROUP,
    SUBENTRY_TYPE_ENERGY_SOURCE,
)

from .conftest import apply_states, MCB_RUMAH, MCB_TOKO

RUMAH_ID = "src_rumah"
TOKO_ID = "src_toko"

RUMAH_SOURCE = {
    "name": "MCB RUMAH",
    CONF_ENERGY_ENTITY_ID: "sensor.mcb_rumah_total_energy",
    CONF_POWER_ENTITY_ID: "sensor.mcb_rumah_phase_a_power",
}
TOKO_SOURCE = {
    "name": "MCB TOKO",
    CONF_ENERGY_ENTITY_ID: "sensor.mcb_toko_total_energy",
    CONF_POWER_ENTITY_ID: "sensor.mcb_toko_phase_a_power",
}


def _subentry(subentry_id, data, subentry_type, title):
    """Bentuk subentry untuk MockConfigEntry."""
    return {
        "data": data,
        "subentry_id": subentry_id,
        "subentry_type": subentry_type,
        "title": title,
        "unique_id": None,
    }


async def _setup(hass: HomeAssistant, *groups: dict) -> MockConfigEntry:
    """Pasang dua Energy Source nyata plus Billing Group yang diminta."""
    await hass.config.async_set_time_zone("Asia/Jakarta")
    subentries = [
        _subentry(RUMAH_ID, RUMAH_SOURCE, SUBENTRY_TYPE_ENERGY_SOURCE, "MCB RUMAH"),
        _subentry(TOKO_ID, TOKO_SOURCE, SUBENTRY_TYPE_ENERGY_SOURCE, "MCB TOKO"),
    ]
    subentries.extend(
        _subentry(
            f"grp_{index}",
            group,
            SUBENTRY_TYPE_BILLING_GROUP,
            group["name"],
        )
        for index, group in enumerate(groups)
    )
    entry = MockConfigEntry(domain=DOMAIN, data={}, subentries_data=subentries)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


PLN_RUMAH = {
    "name": "PLN RUMAH",
    CONF_SOURCE_IDS: [RUMAH_ID],
    CONF_CYCLE_PERIODS: ["hour", "day", "week", "month", "year"],
}


async def test_creates_group_entities(hass: HomeAssistant) -> None:
    """Satu Billing Group menghasilkan total, daya, dan lima penghitung periode."""
    apply_states(hass, MCB_RUMAH, MCB_TOKO)
    await _setup(hass, PLN_RUMAH)

    for entity_id in (
        "sensor.pln_rumah_energy_total",
        "sensor.pln_rumah_power",
        "sensor.pln_rumah_energy_this_hour",
        "sensor.pln_rumah_energy_this_day",
        "sensor.pln_rumah_energy_this_week",
        "sensor.pln_rumah_energy_this_month",
        "sensor.pln_rumah_energy_this_year",
    ):
        assert hass.states.get(entity_id) is not None, entity_id


async def test_only_selected_periods_are_created(hass: HomeAssistant) -> None:
    """Periode yang tidak dicentang user tidak dibuat entity-nya."""
    apply_states(hass, MCB_RUMAH, MCB_TOKO)
    await _setup(
        hass,
        {
            "name": "PLN RUMAH",
            CONF_SOURCE_IDS: [RUMAH_ID],
            CONF_CYCLE_PERIODS: ["day", "month"],
        },
    )

    assert hass.states.get("sensor.pln_rumah_energy_this_day") is not None
    assert hass.states.get("sensor.pln_rumah_energy_this_month") is not None
    assert hass.states.get("sensor.pln_rumah_energy_this_hour") is None
    assert hass.states.get("sensor.pln_rumah_energy_this_week") is None
    assert hass.states.get("sensor.pln_rumah_energy_this_year") is None


async def test_group_total_mirrors_single_member(hass: HomeAssistant) -> None:
    """Grup dengan satu anggota menampilkan angka meteran itu apa adanya."""
    apply_states(hass, MCB_RUMAH, MCB_TOKO)
    await _setup(hass, PLN_RUMAH)

    state = hass.states.get("sensor.pln_rumah_energy_total")
    assert float(state.state) == pytest.approx(15498.27)
    assert state.attributes[ATTR_MEMBER_SOURCES] == ["MCB RUMAH"]


async def test_group_sums_two_members(hass: HomeAssistant) -> None:
    """Grup dengan dua meteran menjumlahkan keduanya."""
    apply_states(hass, MCB_RUMAH, MCB_TOKO)
    await _setup(
        hass,
        {
            "name": "PLN GABUNGAN",
            CONF_SOURCE_IDS: [RUMAH_ID, TOKO_ID],
            CONF_CYCLE_PERIODS: ["day"],
        },
    )

    state = hass.states.get("sensor.pln_gabungan_energy_total")
    assert float(state.state) == pytest.approx(15498.27 + 15114.43)

    power = hass.states.get("sensor.pln_gabungan_power")
    # MCB RUMAH melapor 1,234 kW, MCB TOKO melapor 830 W.
    assert float(power.state) == pytest.approx(1234.0 + 830.0)


async def test_period_counter_tracks_usage(hass: HomeAssistant) -> None:
    """Penghitung harian naik mengikuti pemakaian sejak awal siklus."""
    apply_states(hass, MCB_RUMAH, MCB_TOKO)
    await _setup(hass, PLN_RUMAH)

    assert float(hass.states.get("sensor.pln_rumah_energy_this_day").state) == (
        pytest.approx(0.0)
    )

    hass.states.async_set(
        "sensor.mcb_rumah_total_energy",
        "15501.27",
        MCB_RUMAH["sensor.mcb_rumah_total_energy"][1],
    )
    await hass.async_block_till_done()

    assert float(hass.states.get("sensor.pln_rumah_energy_this_day").state) == (
        pytest.approx(3.0)
    )
    assert float(hass.states.get("sensor.pln_rumah_energy_this_month").state) == (
        pytest.approx(3.0)
    )


async def test_period_counter_resets_at_boundary(
    hass: HomeAssistant, freezer
) -> None:
    """Lewat tengah malam, penghitung harian mulai lagi dari nol dengan sendirinya."""
    await hass.config.async_set_time_zone("Asia/Jakarta")
    # 23:50 waktu Jakarta = 16:50 UTC.
    freezer.move_to("2026-09-03 16:50:00+00:00")
    apply_states(hass, MCB_RUMAH, MCB_TOKO)
    await _setup(hass, PLN_RUMAH)

    hass.states.async_set(
        "sensor.mcb_rumah_total_energy",
        "15500.27",
        MCB_RUMAH["sensor.mcb_rumah_total_energy"][1],
    )
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.pln_rumah_energy_this_day").state) == (
        pytest.approx(2.0)
    )

    # Lewati tengah malam. Timer batas siklus yang harus bekerja, bukan
    # perubahan state - karena tengah malam belum tentu ada pemakaian baru.
    freezer.tick(timedelta(minutes=15))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert float(hass.states.get("sensor.pln_rumah_energy_this_day").state) == (
        pytest.approx(0.0)
    )
    # Bulan belum berganti, jadi penghitung bulanan tetap jalan.
    assert float(hass.states.get("sensor.pln_rumah_energy_this_month").state) == (
        pytest.approx(2.0)
    )


async def test_period_counter_exposes_cycle_boundaries(
    hass: HomeAssistant, freezer
) -> None:
    """Kapan siklus dimulai dan kapan reset berikutnya harus terlihat user."""
    await hass.config.async_set_time_zone("Asia/Jakarta")
    freezer.move_to("2026-09-03 05:00:00+00:00")  # 12:00 WIB
    apply_states(hass, MCB_RUMAH, MCB_TOKO)
    await _setup(hass, PLN_RUMAH)

    state = hass.states.get("sensor.pln_rumah_energy_this_day")
    assert state.attributes["cycle_start"].startswith("2026-09-03T00:00:00")
    assert state.attributes[ATTR_NEXT_CYCLE_START].startswith("2026-09-04T00:00:00")
    # last_reset dipakai Home Assistant untuk statistik sensor state_class total.
    assert state.attributes["last_reset"].startswith("2026-09-03T00:00:00")


async def test_offline_member_is_reported_not_hidden(
    hass: HomeAssistant, freezer
) -> None:
    """Anggota yang mati disebutkan di atribut, bukan disembunyikan diam-diam.

    Kalau satu meteran mati, pemakaiannya memang tidak terhitung. Angka grup
    tetap dipakai, tapi user harus bisa melihat bahwa angka itu tidak lengkap.
    """
    apply_states(hass, MCB_RUMAH, MCB_TOKO)
    await _setup(
        hass,
        {
            "name": "PLN GABUNGAN",
            CONF_SOURCE_IDS: [RUMAH_ID, TOKO_ID],
            CONF_CYCLE_PERIODS: ["day"],
        },
    )

    hass.states.async_set("sensor.mcb_toko_total_energy", "unavailable", {})
    hass.states.async_set("sensor.mcb_toko_phase_a_power", "unavailable", {})
    await hass.async_block_till_done()

    # Lewati masa tenggang supaya MCB TOKO benar-benar dinyatakan offline.
    freezer.tick(timedelta(minutes=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.pln_gabungan_energy_total")
    assert state.attributes[ATTR_MEMBERS_UNAVAILABLE] == ["MCB TOKO"]
    assert "MCB TOKO" in state.attributes[ATTR_MEMBER_SOURCES]
    # Totalnya tidak ikut jatuh walau satu anggota hilang.
    assert float(state.state) == pytest.approx(15498.27 + 15114.43)


async def test_group_state_survives_reload(hass: HomeAssistant) -> None:
    """Penghitung periode tidak kembali ke nol hanya karena integrasi dimuat ulang."""
    apply_states(hass, MCB_RUMAH, MCB_TOKO)
    entry = await _setup(hass, PLN_RUMAH)

    hass.states.async_set(
        "sensor.mcb_rumah_total_energy",
        "15502.27",
        MCB_RUMAH["sensor.mcb_rumah_total_energy"][1],
    )
    await hass.async_block_till_done()
    before = float(hass.states.get("sensor.pln_rumah_energy_this_day").state)
    assert before == pytest.approx(4.0)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert float(hass.states.get("sensor.pln_rumah_energy_this_day").state) == (
        pytest.approx(before)
    )
    assert float(hass.states.get("sensor.pln_rumah_energy_total").state) == (
        pytest.approx(15502.27)
    )


async def test_two_groups_are_independent(hass: HomeAssistant) -> None:
    """PLN RUMAH dan PLN TOKO dihitung terpisah, persis skenario user."""
    apply_states(hass, MCB_RUMAH, MCB_TOKO)
    await _setup(
        hass,
        PLN_RUMAH,
        {
            "name": "PLN TOKO",
            CONF_SOURCE_IDS: [TOKO_ID],
            CONF_CYCLE_PERIODS: ["day"],
        },
    )

    hass.states.async_set(
        "sensor.mcb_toko_total_energy",
        "15120.43",
        MCB_TOKO["sensor.mcb_toko_total_energy"][1],
    )
    await hass.async_block_till_done()

    assert float(hass.states.get("sensor.pln_toko_energy_this_day").state) == (
        pytest.approx(6.0)
    )
    assert float(hass.states.get("sensor.pln_rumah_energy_this_day").state) == (
        pytest.approx(0.0)
    )


async def test_meter_reset_does_not_inflate_period_counter(
    hass: HomeAssistant,
) -> None:
    """Reset counter meteran tidak boleh membuat 'pemakaian hari ini' meledak.

    Total grup tetap naik sesuai aturan HA Core, dan penghitung periode ikut
    naik sebesar itu - tapi tidak pernah jadi angka negatif atau melompat mundur.
    """
    apply_states(hass, MCB_RUMAH, MCB_TOKO)
    await _setup(hass, PLN_RUMAH)
    attributes = MCB_RUMAH["sensor.mcb_rumah_total_energy"][1]

    hass.states.async_set("sensor.mcb_rumah_total_energy", "15500.27", attributes)
    await hass.async_block_till_done()
    hass.states.async_set("sensor.mcb_rumah_total_energy", "0.5", attributes)
    await hass.async_block_till_done()

    daily = float(hass.states.get("sensor.pln_rumah_energy_this_day").state)
    assert daily >= 0
    assert daily == pytest.approx(2.5)
