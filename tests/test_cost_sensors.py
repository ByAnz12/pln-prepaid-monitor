"""Test entity biaya pada Billing Group."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pln_prepaid_monitor.const import (
    ATTR_ACTIVE_RATE,
    ATTR_ENERGY_COST_ONLY,
    ATTR_FIXED_CHARGE_INCLUDED,
    ATTR_RATE_HISTORY,
    ATTR_TARIFF_NAME,
    CONF_CYCLE_PERIODS,
    CONF_ENERGY_ENTITY_ID,
    CONF_FIXED_CHARGE_PERIOD,
    CONF_FIXED_CHARGE_RP,
    CONF_POWER_ENTITY_ID,
    CONF_RATE_HISTORY,
    CONF_RATE_RP_PER_KWH,
    CONF_ROUNDING_MODE,
    CONF_ROUNDING_UNIT_RP,
    CONF_SOURCE_IDS,
    CONF_TARIFF_ID,
    DOMAIN,
    SUBENTRY_TYPE_BILLING_GROUP,
    SUBENTRY_TYPE_ENERGY_SOURCE,
    SUBENTRY_TYPE_TARIFF,
)

from .conftest import apply_states, MCB_RUMAH

RUMAH_ID = "src_rumah"
TARIFF_ID = "tar_r1"
GROUP_ID = "grp_rumah"
RATE = 1444.70

SOURCE_SUBENTRY = {
    "data": {
        "name": "MCB RUMAH",
        CONF_ENERGY_ENTITY_ID: "sensor.mcb_rumah_total_energy",
        CONF_POWER_ENTITY_ID: "sensor.mcb_rumah_phase_a_power",
    },
    "subentry_id": RUMAH_ID,
    "subentry_type": SUBENTRY_TYPE_ENERGY_SOURCE,
    "title": "MCB RUMAH",
    "unique_id": None,
}


def _tariff_subentry(**overrides) -> dict:
    """Subentry tarif dengan nilai bawaan yang masuk akal."""
    data = {
        "name": "Tarif R-1",
        CONF_RATE_RP_PER_KWH: RATE,
        CONF_FIXED_CHARGE_RP: 0.0,
        CONF_FIXED_CHARGE_PERIOD: "monthly",
        CONF_ROUNDING_MODE: "nearest",
        CONF_ROUNDING_UNIT_RP: 1.0,
        CONF_RATE_HISTORY: [
            {"effective_from": "2026-01-01T00:00:00+07:00", "rate_rp_per_kwh": RATE}
        ],
    }
    data.update(overrides)
    return {
        "data": data,
        "subentry_id": TARIFF_ID,
        "subentry_type": SUBENTRY_TYPE_TARIFF,
        "title": data["name"],
        "unique_id": None,
    }


def _group_subentry(tariff_id: str | None = TARIFF_ID) -> dict:
    """Subentry kelompok tagihan yang memakai tarif tersebut."""
    return {
        "data": {
            "name": "PLN RUMAH",
            CONF_SOURCE_IDS: [RUMAH_ID],
            CONF_CYCLE_PERIODS: ["day", "month"],
            CONF_TARIFF_ID: tariff_id,
        },
        "subentry_id": GROUP_ID,
        "subentry_type": SUBENTRY_TYPE_BILLING_GROUP,
        "title": "PLN RUMAH",
        "unique_id": None,
    }


async def _setup(hass: HomeAssistant, *subentries) -> MockConfigEntry:
    """Pasang integrasi dengan subentry yang diberikan.

    Mata uang sengaja diambil dari pengaturan Home Assistant, bukan dipaksa
    "IDR" di dalam kode - jadi test ini juga mengeset mata uang instance-nya.
    """
    await hass.config.async_set_time_zone("Asia/Jakarta")
    await hass.config.async_update(currency="IDR")
    entry = MockConfigEntry(domain=DOMAIN, data={}, subentries_data=list(subentries))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _use_energy(hass: HomeAssistant, reading: str) -> None:
    """Naikkan pembacaan meteran."""
    hass.states.async_set(
        "sensor.mcb_rumah_total_energy",
        reading,
        MCB_RUMAH["sensor.mcb_rumah_total_energy"][1],
    )
    await hass.async_block_till_done()


async def test_cost_entities_created_when_tariff_is_set(
    hass: HomeAssistant,
) -> None:
    """Kelompok bertarif mendapat sensor biaya total dan per periode."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, _tariff_subentry(), _group_subentry())

    for entity_id in (
        "sensor.pln_rumah_cost_total",
        "sensor.pln_rumah_cost_this_day",
        "sensor.pln_rumah_cost_this_month",
    ):
        assert hass.states.get(entity_id) is not None, entity_id


async def test_no_cost_entities_without_tariff(hass: HomeAssistant) -> None:
    """Tanpa tarif, kelompok tetap menghitung kWh tapi tidak punya sensor biaya."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, _group_subentry(tariff_id=None))

    assert hass.states.get("sensor.pln_rumah_energy_this_day") is not None
    assert hass.states.get("sensor.pln_rumah_cost_total") is None
    assert hass.states.get("sensor.pln_rumah_cost_this_day") is None


async def test_cost_follows_usage(hass: HomeAssistant) -> None:
    """Pemakaian 10 kWh pada tarif Rp 1.444,70 jadi Rp 14.447."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, _tariff_subentry(), _group_subentry())

    assert float(hass.states.get("sensor.pln_rumah_cost_total").state) == (
        pytest.approx(0.0)
    )

    await _use_energy(hass, "15508.27")  # naik 10 kWh

    assert float(hass.states.get("sensor.pln_rumah_cost_total").state) == (
        pytest.approx(14447.0)
    )
    assert float(hass.states.get("sensor.pln_rumah_cost_this_day").state) == (
        pytest.approx(14447.0)
    )


async def test_cost_uses_monetary_total_state_class(hass: HomeAssistant) -> None:
    """device_class monetary hanya boleh dipasangkan dengan state_class total.

    Home Assistant Core 2026.8.3 hanya mengizinkan kombinasi itu; memakai
    total_increasing akan memicu peringatan di log.
    """
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, _tariff_subentry(), _group_subentry())

    state = hass.states.get("sensor.pln_rumah_cost_total")
    assert state.attributes["device_class"] == "monetary"
    assert state.attributes["state_class"] == "total"
    assert state.attributes["unit_of_measurement"] == "IDR"


async def test_cost_attributes_expose_tariff(hass: HomeAssistant) -> None:
    """Tarif aktif dan riwayatnya terlihat di atribut, untuk audit."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, _tariff_subentry(), _group_subentry())

    attributes = hass.states.get("sensor.pln_rumah_cost_total").attributes
    assert attributes[ATTR_TARIFF_NAME] == "Tarif R-1"
    assert attributes[ATTR_ACTIVE_RATE] == pytest.approx(RATE)
    assert len(attributes[ATTR_RATE_HISTORY]) == 1


async def test_tariff_change_is_not_retroactive(hass: HomeAssistant) -> None:
    """Menaikkan tarif tidak mengubah biaya yang sudah tercatat (spec K.7).

    Skenario nyata: PLN menaikkan tarif di tengah bulan. Pemakaian sebelum
    kenaikan harus tetap dihitung dengan tarif lama.
    """
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(
        hass,
        SOURCE_SUBENTRY,
        _tariff_subentry(**{CONF_RATE_RP_PER_KWH: 1000.0}),
        _group_subentry(),
    )

    await _use_energy(hass, "15508.27")  # 10 kWh @ Rp 1.000 = Rp 10.000
    assert float(hass.states.get("sensor.pln_rumah_cost_total").state) == (
        pytest.approx(10000.0)
    )

    # Tarif naik jadi Rp 1.500.
    tariff_subentry = entry.subentries[TARIFF_ID]
    hass.config_entries.async_update_subentry(
        entry,
        tariff_subentry,
        data={**tariff_subentry.data, CONF_RATE_RP_PER_KWH: 1500.0},
    )
    await hass.async_block_till_done()

    await _use_energy(hass, "15518.27")  # 10 kWh lagi @ Rp 1.500

    total = float(hass.states.get("sensor.pln_rumah_cost_total").state)
    assert total == pytest.approx(25000.0)
    # Bukan 20 kWh x Rp 1.500 = Rp 30.000.
    assert total != pytest.approx(30000.0)


async def test_rounding_is_applied_to_display_only(hass: HomeAssistant) -> None:
    """Pembulatan mengubah tampilan, bukan angka yang dipakai menghitung."""
    apply_states(hass, MCB_RUMAH)
    await _setup(
        hass,
        SOURCE_SUBENTRY,
        _tariff_subentry(**{CONF_ROUNDING_UNIT_RP: 1000.0}),
        _group_subentry(),
    )

    await _use_energy(hass, "15508.27")  # Rp 14.447 sebelum dibulatkan

    assert float(hass.states.get("sensor.pln_rumah_cost_total").state) == (
        pytest.approx(14000.0)
    )

    # Pemakaian berikutnya tetap dihitung dari angka penuh, bukan dari 14.000.
    await _use_energy(hass, "15518.27")
    assert float(hass.states.get("sensor.pln_rumah_cost_total").state) == (
        pytest.approx(29000.0)
    )


async def test_fixed_charge_only_on_month_and_year(hass: HomeAssistant) -> None:
    """Biaya beban masuk ke penghitung bulanan, tidak ke harian (spec F.3)."""
    apply_states(hass, MCB_RUMAH)
    await _setup(
        hass,
        SOURCE_SUBENTRY,
        _tariff_subentry(
            **{CONF_FIXED_CHARGE_RP: 30000.0, CONF_FIXED_CHARGE_PERIOD: "monthly"}
        ),
        _group_subentry(),
    )

    await _use_energy(hass, "15508.27")

    daily = hass.states.get("sensor.pln_rumah_cost_this_day")
    monthly = hass.states.get("sensor.pln_rumah_cost_this_month")

    assert daily.attributes[ATTR_FIXED_CHARGE_INCLUDED] == pytest.approx(0.0)
    assert monthly.attributes[ATTR_FIXED_CHARGE_INCLUDED] > 0
    # Atribut memisahkan biaya energi dari biaya beban, supaya jelas asalnya.
    assert monthly.attributes[ATTR_ENERGY_COST_ONLY] == pytest.approx(14447.0)
    assert float(monthly.state) > float(daily.state)


async def test_cost_survives_reload(hass: HomeAssistant) -> None:
    """Total biaya tidak kembali ke nol hanya karena integrasi dimuat ulang."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(
        hass, SOURCE_SUBENTRY, _tariff_subentry(), _group_subentry()
    )

    await _use_energy(hass, "15508.27")
    before = float(hass.states.get("sensor.pln_rumah_cost_total").state)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert float(hass.states.get("sensor.pln_rumah_cost_total").state) == (
        pytest.approx(before)
    )

    # Dan pemakaian berikutnya tetap menyambung, bukan dihitung ulang dari nol.
    await _use_energy(hass, "15518.27")
    assert float(hass.states.get("sensor.pln_rumah_cost_total").state) == (
        pytest.approx(before + 10 * RATE)
    )


async def test_cost_ignores_meter_reset_direction(hass: HomeAssistant) -> None:
    """Reset counter meteran tidak boleh membuat biaya jadi negatif."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, _tariff_subentry(), _group_subentry())

    await _use_energy(hass, "15508.27")
    before = float(hass.states.get("sensor.pln_rumah_cost_total").state)

    await _use_energy(hass, "0.5")

    after = float(hass.states.get("sensor.pln_rumah_cost_total").state)
    assert after >= before
