"""Test layanan token pada instance Home Assistant sungguhan."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pln_prepaid_monitor.const import (
    ATTR_HOLD_RESET_TO,
    ATTR_LEDGER_ON_HOLD,
    ATTR_TOPUP_COUNT,
    ATTR_TOTAL_CREDITED,
    CONF_CYCLE_PERIODS,
    CONF_ENERGY_ENTITY_ID,
    CONF_FIXED_CHARGE_PERIOD,
    CONF_FIXED_CHARGE_RP,
    CONF_RATE_HISTORY,
    CONF_RATE_RP_PER_KWH,
    CONF_RESET_HOLD_THRESHOLD_KWH,
    CONF_ROUNDING_MODE,
    CONF_ROUNDING_UNIT_RP,
    CONF_SOURCE_IDS,
    CONF_TARIFF_ID,
    CONF_TOKEN_ENABLED,
    DOMAIN,
    SERVICE_ADD_TOKEN_TOPUP,
    SERVICE_CALIBRATE_TOKEN_READING,
    SERVICE_DELETE_TOPUP,
    SERVICE_EDIT_TOPUP,
    SERVICE_RESET_TOKEN_LEDGER,
    SERVICE_RESOLVE_LEDGER_HOLD,
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
    },
    "subentry_id": RUMAH_ID,
    "subentry_type": SUBENTRY_TYPE_ENERGY_SOURCE,
    "title": "MCB RUMAH",
    "unique_id": None,
}

TARIFF_SUBENTRY = {
    "data": {
        "name": "Tarif R-1",
        CONF_RATE_RP_PER_KWH: RATE,
        CONF_FIXED_CHARGE_RP: 0.0,
        CONF_FIXED_CHARGE_PERIOD: "monthly",
        CONF_ROUNDING_MODE: "nearest",
        CONF_ROUNDING_UNIT_RP: 1.0,
        CONF_RATE_HISTORY: [],
    },
    "subentry_id": TARIFF_ID,
    "subentry_type": SUBENTRY_TYPE_TARIFF,
    "title": "Tarif R-1",
    "unique_id": None,
}


def _group_subentry(token_enabled: bool = True, threshold: float = 1.0) -> dict:
    """Subentry kelompok tagihan dengan pencatatan token."""
    return {
        "data": {
            "name": "PLN RUMAH",
            CONF_SOURCE_IDS: [RUMAH_ID],
            CONF_CYCLE_PERIODS: ["day"],
            CONF_TARIFF_ID: TARIFF_ID,
            CONF_TOKEN_ENABLED: token_enabled,
            CONF_RESET_HOLD_THRESHOLD_KWH: threshold,
        },
        "subentry_id": GROUP_ID,
        "subentry_type": SUBENTRY_TYPE_BILLING_GROUP,
        "title": "PLN RUMAH",
        "unique_id": None,
    }


async def _setup(hass: HomeAssistant, *subentries) -> MockConfigEntry:
    """Pasang integrasi lengkap."""
    await hass.config.async_set_time_zone("Asia/Jakarta")
    await hass.config.async_update(currency="IDR")
    entry = MockConfigEntry(domain=DOMAIN, data={}, subentries_data=list(subentries))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _group_device_id(hass: HomeAssistant) -> str:
    """Id perangkat kelompok tagihan, yang jadi target layanan."""
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, GROUP_ID)})
    assert device is not None
    return device.id


async def _call(hass: HomeAssistant, service: str, **data) -> None:
    """Panggil layanan dengan target perangkat kelompok tagihan."""
    await hass.services.async_call(
        DOMAIN,
        service,
        {"device_id": [_group_device_id(hass)], **data},
        blocking=True,
    )
    await hass.async_block_till_done()


async def _use_energy(hass: HomeAssistant, reading: str) -> None:
    """Naikkan pembacaan meteran."""
    hass.states.async_set(
        "sensor.mcb_rumah_total_energy",
        reading,
        MCB_RUMAH["sensor.mcb_rumah_total_energy"][1],
    )
    await hass.async_block_till_done()


async def test_token_entities_created_when_enabled(hass: HomeAssistant) -> None:
    """Kelompok bertoken mendapat sensor sisa, terpakai, nilai, dan penahanan."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())

    for entity_id in (
        "sensor.pln_rumah_token_remaining",
        "sensor.pln_rumah_token_consumed",
        "sensor.pln_rumah_token_remaining_value",
        "binary_sensor.pln_rumah_token_ledger_hold",
    ):
        assert hass.states.get(entity_id) is not None, entity_id


async def test_no_token_entities_when_disabled(hass: HomeAssistant) -> None:
    """Tanpa pencatatan token, tidak ada satu pun entity token dibuat."""
    apply_states(hass, MCB_RUMAH)
    await _setup(
        hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry(token_enabled=False)
    )

    assert hass.states.get("sensor.pln_rumah_token_remaining") is None
    assert hass.states.get("binary_sensor.pln_rumah_token_ledger_hold") is None


async def test_add_topup_then_usage_reduces_remaining(
    hass: HomeAssistant,
) -> None:
    """Alur normal: isi token, lalu pakai listrik, sisanya berkurang."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())

    await _call(hass, SERVICE_ADD_TOKEN_TOPUP, kwh_credited=50.0, nominal_rp=75000)

    state = hass.states.get("sensor.pln_rumah_token_remaining")
    assert float(state.state) == pytest.approx(50.0)
    assert state.attributes[ATTR_TOTAL_CREDITED] == pytest.approx(50.0)
    assert state.attributes[ATTR_TOPUP_COUNT] == 1
    assert state.attributes["device_class"] == "energy_storage"
    assert state.attributes["state_class"] == "measurement"

    await _use_energy(hass, "15510.27")  # pakai 12 kWh

    assert float(hass.states.get("sensor.pln_rumah_token_remaining").state) == (
        pytest.approx(38.0)
    )
    assert float(hass.states.get("sensor.pln_rumah_token_consumed").state) == (
        pytest.approx(12.0)
    )


async def test_topup_is_additive(hass: HomeAssistant) -> None:
    """Isi ulang sebelum habis menambah ke sisa lama, bukan menggantikan."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())

    await _call(hass, SERVICE_ADD_TOKEN_TOPUP, kwh_credited=50.0)
    await _use_energy(hass, "15518.27")  # pakai 20 kWh, sisa 30
    await _call(hass, SERVICE_ADD_TOKEN_TOPUP, kwh_credited=40.0)

    assert float(hass.states.get("sensor.pln_rumah_token_remaining").state) == (
        pytest.approx(70.0)
    )


async def test_token_value_uses_the_tariff(hass: HomeAssistant) -> None:
    """Nilai sisa token = sisa kWh dikali tarif yang berlaku."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())

    await _call(hass, SERVICE_ADD_TOKEN_TOPUP, kwh_credited=10.0)

    state = hass.states.get("sensor.pln_rumah_token_remaining_value")
    assert float(state.state) == pytest.approx(round(10 * RATE))
    # Ditandai jelas bahwa ini bukan harga isi ulang.
    assert state.attributes["excludes_admin_fee_and_ppj"] is True


async def test_edit_and_delete_topup(hass: HomeAssistant) -> None:
    """Salah input bisa diperbaiki dan dihapus, totalnya dihitung ulang."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())

    await _call(hass, SERVICE_ADD_TOKEN_TOPUP, kwh_credited=500.0)  # salah ketik
    history = hass.states.get("sensor.pln_rumah_token_remaining").attributes[
        "topup_history"
    ]
    topup_id = history[0]["id"]

    await _call(hass, SERVICE_EDIT_TOPUP, topup_id=topup_id, kwh_credited=50.0)
    assert float(hass.states.get("sensor.pln_rumah_token_remaining").state) == (
        pytest.approx(50.0)
    )

    await _call(hass, SERVICE_DELETE_TOPUP, topup_id=topup_id)
    assert float(hass.states.get("sensor.pln_rumah_token_remaining").state) == (
        pytest.approx(0.0)
    )


async def test_calibrate_matches_meter_reading(hass: HomeAssistant) -> None:
    """Kalibrasi menyamakan hitungan dengan angka di layar meteran."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())

    await _call(hass, SERVICE_ADD_TOKEN_TOPUP, kwh_credited=50.0)
    await _use_energy(hass, "15508.27")  # sistem mengira sisa 40

    await _call(
        hass, SERVICE_CALIBRATE_TOKEN_READING, actual_remaining_kwh=35.0
    )

    assert float(hass.states.get("sensor.pln_rumah_token_remaining").state) == (
        pytest.approx(35.0)
    )
    await _use_energy(hass, "15513.27")  # pakai 5 kWh lagi
    assert float(hass.states.get("sensor.pln_rumah_token_remaining").state) == (
        pytest.approx(30.0)
    )


async def test_reset_ledger_starts_over(hass: HomeAssistant) -> None:
    """Reset penuh dipakai saat meteran fisik diganti."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())

    await _call(hass, SERVICE_ADD_TOKEN_TOPUP, kwh_credited=50.0)
    await _call(hass, SERVICE_RESET_TOKEN_LEDGER, note="meteran diganti")

    assert float(hass.states.get("sensor.pln_rumah_token_remaining").state) == (
        pytest.approx(0.0)
    )


async def test_ledger_survives_reload(hass: HomeAssistant) -> None:
    """Riwayat token tidak hilang saat integrasi dimuat ulang."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())

    await _call(hass, SERVICE_ADD_TOKEN_TOPUP, kwh_credited=50.0)
    await _use_energy(hass, "15508.27")
    before = float(hass.states.get("sensor.pln_rumah_token_remaining").state)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert float(hass.states.get("sensor.pln_rumah_token_remaining").state) == (
        pytest.approx(before)
    )


async def test_service_refuses_when_token_disabled(hass: HomeAssistant) -> None:
    """Layanan token menolak dengan jelas kalau fiturnya belum diaktifkan."""
    apply_states(hass, MCB_RUMAH)
    await _setup(
        hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry(token_enabled=False)
    )

    with pytest.raises(ServiceValidationError):
        await _call(hass, SERVICE_ADD_TOKEN_TOPUP, kwh_credited=50.0)


async def test_edit_unknown_topup_raises(hass: HomeAssistant) -> None:
    """Kode entri yang tidak ada dilaporkan ke user, bukan diabaikan."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())
    await _call(hass, SERVICE_ADD_TOKEN_TOPUP, kwh_credited=50.0)

    with pytest.raises(ServiceValidationError):
        await _call(hass, SERVICE_EDIT_TOPUP, topup_id="tidak-ada", kwh_credited=1.0)


# --- penahanan ledger saat reset besar (D-007) -------------------------------


async def test_large_meter_reset_holds_the_ledger(hass: HomeAssistant) -> None:
    """Reset besar membekukan sisa token dan meminta keputusan user.

    Ini pengaman yang Anda setujui: daripada diam-diam memotong sisa token
    sampai nol dan memicu peringatan palsu, sistem berhenti dan bertanya.
    """
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())
    await _call(hass, SERVICE_ADD_TOKEN_TOPUP, kwh_credited=50.0)

    # Meteran diganti: pembacaan jatuh, lalu langsung menunjukkan angka besar.
    await _use_energy(hass, "9000")

    assert hass.states.get("binary_sensor.pln_rumah_token_ledger_hold").state == "on"
    state = hass.states.get("sensor.pln_rumah_token_remaining")
    assert state.attributes[ATTR_LEDGER_ON_HOLD] is True
    assert state.attributes[ATTR_HOLD_RESET_TO] == pytest.approx(9000.0)
    # Sisa token TIDAK ikut hangus.
    assert float(state.state) == pytest.approx(50.0)


async def test_small_firmware_reset_does_not_hold(hass: HomeAssistant) -> None:
    """Reset firmware biasa yang jatuh ke hampir nol tidak mengganggu user."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())
    await _call(hass, SERVICE_ADD_TOKEN_TOPUP, kwh_credited=50.0)

    await _use_energy(hass, "0.5")

    assert hass.states.get("binary_sensor.pln_rumah_token_ledger_hold").state == "off"


async def test_resolve_hold_with_ignore(hass: HomeAssistant) -> None:
    """Keputusan 'abaikan' melepas penahanan tanpa memotong sisa token."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())
    await _call(hass, SERVICE_ADD_TOKEN_TOPUP, kwh_credited=50.0)
    await _use_energy(hass, "9000")

    await _call(hass, SERVICE_RESOLVE_LEDGER_HOLD, action="ignore")

    assert hass.states.get("binary_sensor.pln_rumah_token_ledger_hold").state == "off"
    assert float(hass.states.get("sensor.pln_rumah_token_remaining").state) == (
        pytest.approx(50.0)
    )

    # Pemakaian sesudahnya kembali terhitung normal.
    await _use_energy(hass, "9005")
    assert float(hass.states.get("sensor.pln_rumah_token_remaining").state) == (
        pytest.approx(45.0)
    )


async def test_resolve_hold_with_calibration(hass: HomeAssistant) -> None:
    """Keputusan 'kalibrasi' memakai angka yang dibaca user dari meteran."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())
    await _call(hass, SERVICE_ADD_TOKEN_TOPUP, kwh_credited=50.0)
    await _use_energy(hass, "9000")

    await _call(
        hass,
        SERVICE_RESOLVE_LEDGER_HOLD,
        action="calibrate",
        actual_remaining_kwh=12.5,
    )

    assert float(hass.states.get("sensor.pln_rumah_token_remaining").state) == (
        pytest.approx(12.5)
    )


async def test_calibrate_action_requires_reading(hass: HomeAssistant) -> None:
    """Kalibrasi tanpa angka meteran ditolak dengan pesan yang jelas."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())
    await _call(hass, SERVICE_ADD_TOKEN_TOPUP, kwh_credited=50.0)
    await _use_energy(hass, "9000")

    with pytest.raises(ServiceValidationError):
        await _call(hass, SERVICE_RESOLVE_LEDGER_HOLD, action="calibrate")


async def test_resolve_without_hold_raises(hass: HomeAssistant) -> None:
    """Tidak ada penahanan berarti tidak ada yang perlu diputuskan."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())
    await _call(hass, SERVICE_ADD_TOKEN_TOPUP, kwh_credited=50.0)

    with pytest.raises(ServiceValidationError):
        await _call(hass, SERVICE_RESOLVE_LEDGER_HOLD, action="accept")


async def test_hold_survives_reload(hass: HomeAssistant) -> None:
    """Penahanan tetap ada setelah restart, tidak hilang diam-diam."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())
    await _call(hass, SERVICE_ADD_TOKEN_TOPUP, kwh_credited=50.0)
    await _use_energy(hass, "9000")

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.pln_rumah_token_ledger_hold").state == "on"
    assert float(hass.states.get("sensor.pln_rumah_token_remaining").state) == (
        pytest.approx(50.0)
    )
