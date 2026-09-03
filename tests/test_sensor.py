"""Test entity kanonik yang dihasilkan dari Energy Source nyata."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.pln_prepaid_monitor.const import (
    ATTR_LAST_RESET_AT,
    ATTR_LAST_RESET_FROM,
    ATTR_LAST_RESET_TO,
    ATTR_RESETS_DETECTED,
    ATTR_SOURCE_OF_TRUTH,
    ATTR_SOURCE_RAW_VALUE,
    ATTR_SOURCE_UNIT,
    ATTR_UNIT_CONVERSION_FACTOR,
    CONF_CURRENT_ENTITY_ID,
    CONF_ENERGY_ENTITY_ID,
    CONF_FREQUENCY_ENTITY_ID,
    CONF_POWER_ENTITY_ID,
    CONF_UNAVAILABLE_GRACE_MINUTES,
    CONF_VOLTAGE_ENTITY_ID,
    DOMAIN,
    SUBENTRY_TYPE_ENERGY_SOURCE,
)

from .conftest import apply_states, MCB_RUMAH, MCB_TOKO

MCB_RUMAH_SOURCE = {
    "name": "MCB RUMAH",
    CONF_ENERGY_ENTITY_ID: "sensor.mcb_rumah_total_energy",
    CONF_POWER_ENTITY_ID: "sensor.mcb_rumah_phase_a_power",
    CONF_VOLTAGE_ENTITY_ID: "sensor.mcb_rumah_phase_a_voltage",
    CONF_CURRENT_ENTITY_ID: "sensor.mcb_rumah_phase_a_current",
    CONF_FREQUENCY_ENTITY_ID: "sensor.mcb_rumah_supply_frequency",
    CONF_UNAVAILABLE_GRACE_MINUTES: 5,
}


async def _setup(hass: HomeAssistant, *sources: dict) -> MockConfigEntry:
    """Pasang integrasi dengan sejumlah Energy Source."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        subentries_data=[
            {
                "data": source,
                "subentry_type": SUBENTRY_TYPE_ENERGY_SOURCE,
                "title": source["name"],
                "unique_id": None,
            }
            for source in sources
        ],
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_creates_canonical_entities(hass: HomeAssistant) -> None:
    """Satu Energy Source menghasilkan lima sensor plus status koneksi."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, MCB_RUMAH_SOURCE)

    for entity_id in (
        "sensor.mcb_rumah_energy",
        "sensor.mcb_rumah_power",
        "sensor.mcb_rumah_voltage",
        "sensor.mcb_rumah_current",
        "sensor.mcb_rumah_frequency",
        "binary_sensor.mcb_rumah_connection_status",
    ):
        assert hass.states.get(entity_id) is not None, entity_id


async def test_power_in_kw_is_published_in_watt(hass: HomeAssistant) -> None:
    """1,234 kW dari MCB RUMAH harus terbit sebagai 1234 W."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, MCB_RUMAH_SOURCE)

    state = hass.states.get("sensor.mcb_rumah_power")
    assert float(state.state) == pytest.approx(1234.0)
    assert state.attributes["unit_of_measurement"] == "W"
    assert state.attributes[ATTR_SOURCE_UNIT] == "kW"
    assert state.attributes[ATTR_UNIT_CONVERSION_FACTOR] == pytest.approx(1000.0)
    assert state.attributes[ATTR_SOURCE_RAW_VALUE] == pytest.approx(1.234)


async def test_energy_mirrors_meter_reading_then_accumulates(
    hass: HomeAssistant,
) -> None:
    """Angka energi mengikuti 'total forward energy' meter, lalu ikut bertambah."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, MCB_RUMAH_SOURCE)

    state = hass.states.get("sensor.mcb_rumah_energy")
    assert float(state.state) == pytest.approx(15498.27)
    assert state.attributes[ATTR_SOURCE_OF_TRUTH] == "cumulative"

    hass.states.async_set(
        "sensor.mcb_rumah_total_energy",
        "15500.27",
        MCB_RUMAH["sensor.mcb_rumah_total_energy"][1],
    )
    await hass.async_block_till_done()

    assert float(hass.states.get("sensor.mcb_rumah_energy").state) == pytest.approx(
        15500.27
    )


async def test_energy_never_drops_when_meter_resets(hass: HomeAssistant) -> None:
    """Reset counter fisik tidak boleh membuat angka kita ikut jatuh."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, MCB_RUMAH_SOURCE)
    attributes = MCB_RUMAH["sensor.mcb_rumah_total_energy"][1]

    hass.states.async_set("sensor.mcb_rumah_total_energy", "15510.00", attributes)
    await hass.async_block_till_done()
    before = float(hass.states.get("sensor.mcb_rumah_energy").state)

    # Meter diganti / firmware reset: pembacaan jatuh ke hampir nol.
    hass.states.async_set("sensor.mcb_rumah_total_energy", "0.5", attributes)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.mcb_rumah_energy")
    assert float(state.state) >= before
    assert float(state.state) == pytest.approx(15510.50)
    assert state.attributes[ATTR_RESETS_DETECTED] == 1

    # Detail untuk diagnosis meteran yang diduga ter-reset sendiri.
    assert state.attributes[ATTR_LAST_RESET_FROM] == pytest.approx(15510.00)
    assert state.attributes[ATTR_LAST_RESET_TO] == pytest.approx(0.5)
    assert state.attributes[ATTR_LAST_RESET_AT] is not None


async def test_negative_reading_does_not_corrupt_total(hass: HomeAssistant) -> None:
    """Pembacaan negatif diabaikan, total tetap seperti sebelumnya."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, MCB_RUMAH_SOURCE)
    attributes = MCB_RUMAH["sensor.mcb_rumah_total_energy"][1]

    hass.states.async_set("sensor.mcb_rumah_total_energy", "-3", attributes)
    await hass.async_block_till_done()

    assert float(hass.states.get("sensor.mcb_rumah_energy").state) == pytest.approx(
        15498.27
    )


async def test_holds_last_power_value_during_grace_period(
    hass: HomeAssistant,
) -> None:
    """Gangguan singkat: nilai daya ditahan, bukan dijatuhkan ke nol (K.1)."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, MCB_RUMAH_SOURCE)

    hass.states.async_set("sensor.mcb_rumah_phase_a_power", STATE_UNAVAILABLE, {})
    hass.states.async_set("sensor.mcb_rumah_total_energy", STATE_UNAVAILABLE, {})
    await hass.async_block_till_done()

    power = hass.states.get("sensor.mcb_rumah_power")
    assert float(power.state) == pytest.approx(1234.0)
    assert power.attributes["holding_last_value"] is True
    assert hass.states.get("binary_sensor.mcb_rumah_connection_status").state == "on"


async def test_marks_source_offline_after_grace_period(
    hass: HomeAssistant,
) -> None:
    """Hilang lebih lama dari masa tenggang: sumber dinyatakan offline (K.2)."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, MCB_RUMAH_SOURCE)

    hass.states.async_set("sensor.mcb_rumah_total_energy", STATE_UNAVAILABLE, {})
    hass.states.async_set("sensor.mcb_rumah_phase_a_power", STATE_UNAVAILABLE, {})
    await hass.async_block_till_done()

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.mcb_rumah_connection_status").state == "off"
    assert hass.states.get("sensor.mcb_rumah_power").state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.mcb_rumah_energy").state == STATE_UNAVAILABLE


async def test_accumulator_survives_reload(hass: HomeAssistant) -> None:
    """Restart/reload tidak boleh menghilangkan akumulasi yang sudah berjalan."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, MCB_RUMAH_SOURCE)
    attributes = MCB_RUMAH["sensor.mcb_rumah_total_energy"][1]

    hass.states.async_set("sensor.mcb_rumah_total_energy", "15510.00", attributes)
    await hass.async_block_till_done()
    hass.states.async_set("sensor.mcb_rumah_total_energy", "0.5", attributes)
    await hass.async_block_till_done()

    before = float(hass.states.get("sensor.mcb_rumah_energy").state)
    resets_before = hass.states.get("sensor.mcb_rumah_energy").attributes[
        ATTR_RESETS_DETECTED
    ]

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.mcb_rumah_energy")
    assert float(state.state) == pytest.approx(before)
    assert state.attributes[ATTR_RESETS_DETECTED] == resets_before


async def test_two_billing_sources_are_independent(hass: HomeAssistant) -> None:
    """MCB RUMAH dan MCB TOKO berjalan sendiri-sendiri tanpa saling mengganggu."""
    apply_states(hass, MCB_RUMAH, MCB_TOKO)
    await _setup(
        hass,
        MCB_RUMAH_SOURCE,
        {
            "name": "MCB TOKO",
            CONF_ENERGY_ENTITY_ID: "sensor.mcb_toko_total_energy",
            CONF_POWER_ENTITY_ID: "sensor.mcb_toko_phase_a_power",
        },
    )

    assert float(hass.states.get("sensor.mcb_rumah_energy").state) == pytest.approx(
        15498.27
    )
    assert float(hass.states.get("sensor.mcb_toko_energy").state) == pytest.approx(
        15114.43
    )

    hass.states.async_set(
        "sensor.mcb_toko_total_energy",
        "15120.43",
        MCB_TOKO["sensor.mcb_toko_total_energy"][1],
    )
    await hass.async_block_till_done()

    assert float(hass.states.get("sensor.mcb_rumah_energy").state) == pytest.approx(
        15498.27
    )
    assert float(hass.states.get("sensor.mcb_toko_energy").state) == pytest.approx(
        15120.43
    )


async def test_power_only_source_is_integrated_and_flagged(
    hass: HomeAssistant, freezer
) -> None:
    """Tanpa sensor kWh, energi diperkirakan dari daya dan ditandai estimasi."""
    hass.states.async_set(
        "sensor.hanya_daya",
        "1000",
        {
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
        },
    )
    await _setup(
        hass,
        {"name": "Hanya Daya", CONF_POWER_ENTITY_ID: "sensor.hanya_daya"},
    )

    state = hass.states.get("sensor.hanya_daya_energy")
    assert state.attributes[ATTR_SOURCE_OF_TRUTH] == "integrated_from_power"

    # Beban stabil: sensor melaporkan angka yang sama, jadi Home Assistant tidak
    # mengirim event perubahan sama sekali. Sampler berkala yang harus menjaga
    # akumulasi tetap jalan.
    freezer.tick(timedelta(hours=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # 1000 W selama 1 jam = 1 kWh.
    assert float(hass.states.get("sensor.hanya_daya_energy").state) == pytest.approx(
        1.0, abs=0.01
    )
