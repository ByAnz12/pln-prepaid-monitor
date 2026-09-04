"""Pencatatan token dan pengaturan lewat isian dan tombol di dashboard.

Yang diuji di sini adalah janji yang dibuat ke user: seluruh urusan token bisa
diselesaikan dari dashboard, tanpa membuka Developer Tools sama sekali.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pln_prepaid_monitor.const import (
    CONF_CRITICAL_THRESHOLD_DAYS,
    CONF_CYCLE_PERIODS,
    CONF_ENERGY_ENTITY_ID,
    CONF_RATE_HISTORY,
    CONF_RATE_RP_PER_KWH,
    CONF_SOURCE_IDS,
    CONF_TARIFF_ID,
    CONF_TOKEN_ENABLED,
    CONF_WARNING_THRESHOLD_DAYS,
    DOMAIN,
    SUBENTRY_TYPE_BILLING_GROUP,
    SUBENTRY_TYPE_ENERGY_SOURCE,
    SUBENTRY_TYPE_TARIFF,
)

from custom_components.pln_prepaid_monitor.dashboard import view_cards

from .conftest import apply_states, MCB_RUMAH

RUMAH_ID = "src_rumah"
GROUP_ID = "grp_rumah"
TARIFF_ID = "trf_r1"

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
        CONF_RATE_RP_PER_KWH: 1444.70,
        CONF_RATE_HISTORY: [],
    },
    "subentry_id": TARIFF_ID,
    "subentry_type": SUBENTRY_TYPE_TARIFF,
    "title": "Tarif R-1",
    "unique_id": None,
}

GROUP_SUBENTRY = {
    "data": {
        "name": "PLN RUMAH",
        CONF_SOURCE_IDS: [RUMAH_ID],
        CONF_CYCLE_PERIODS: ["day"],
        CONF_TARIFF_ID: TARIFF_ID,
        CONF_TOKEN_ENABLED: True,
    },
    "subentry_id": GROUP_ID,
    "subentry_type": SUBENTRY_TYPE_BILLING_GROUP,
    "title": "PLN RUMAH",
    "unique_id": None,
}


async def _setup(
    hass: HomeAssistant, group_overrides: dict | None = None
) -> MockConfigEntry:
    """Pasang integrasi lengkap dengan tarif dan token."""
    await hass.config.async_set_time_zone("Asia/Jakarta")
    await hass.config.async_update(currency="IDR")
    group = {
        **GROUP_SUBENTRY,
        "data": {**GROUP_SUBENTRY["data"], **(group_overrides or {})},
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        subentries_data=[SOURCE_SUBENTRY, TARIFF_SUBENTRY, group],
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity_id(hass: HomeAssistant, platform: str, key: str) -> str:
    """entity_id milik satu peran, dicari lewat unique_id-nya."""
    from homeassistant.helpers import entity_registry as er

    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{GROUP_ID}_{key}"
    )
    assert entity_id, f"entity {platform}.{key} tidak dibuat"
    return entity_id


async def _set_number(hass: HomeAssistant, entity_id: str, value: float) -> None:
    await hass.services.async_call(
        "number", "set_value", {"entity_id": entity_id, "value": value}, blocking=True
    )
    await hass.async_block_till_done()


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )
    await hass.async_block_till_done()


# --- pencatatan pengisian ----------------------------------------------------


async def test_topup_from_the_dashboard_without_developer_tools(
    hass: HomeAssistant,
) -> None:
    """Isi angkanya, tekan tombolnya, sisa token bertambah."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)
    group = entry.runtime_data.billing_groups[GROUP_ID]

    await _set_number(hass, _entity_id(hass, "number", "topup_kwh"), 826.50)
    await _press(hass, _entity_id(hass, "button", "record_topup"))

    assert group.token_remaining_kwh == pytest.approx(826.50, abs=0.01)


async def test_the_amount_is_cleared_so_it_cannot_be_recorded_twice(
    hass: HomeAssistant,
) -> None:
    """Sesudah dicatat, isiannya dikosongkan supaya tidak tertekan dua kali."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)
    group = entry.runtime_data.billing_groups[GROUP_ID]
    amount = _entity_id(hass, "number", "topup_kwh")
    button = _entity_id(hass, "button", "record_topup")

    await _set_number(hass, amount, 826.50)
    await _press(hass, button)

    assert hass.states.get(amount).state == "0.0"

    # Tekanan kedua tanpa mengisi ulang harus ditolak, bukan mencatat nol.
    with pytest.raises(HomeAssistantError):
        await _press(hass, button)

    assert group.token_remaining_kwh == pytest.approx(826.50, abs=0.01)


async def test_pressing_with_an_empty_amount_is_refused(hass: HomeAssistant) -> None:
    """Menekan tombol tanpa mengisi angka lebih mungkin lupa daripada disengaja."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)
    group = entry.runtime_data.billing_groups[GROUP_ID]

    with pytest.raises(HomeAssistantError):
        await _press(hass, _entity_id(hass, "button", "record_topup"))

    assert group.ledger.state.entries == []


async def test_calibrating_from_the_dashboard(hass: HomeAssistant) -> None:
    """Angka di layar meteran bisa disamakan tanpa membuka Developer Tools."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)
    group = entry.runtime_data.billing_groups[GROUP_ID]

    await _set_number(hass, _entity_id(hass, "number", "topup_kwh"), 826.50)
    await _press(hass, _entity_id(hass, "button", "record_topup"))

    await _set_number(hass, _entity_id(hass, "number", "meter_reading_kwh"), 500.0)
    await _press(hass, _entity_id(hass, "button", "calibrate_token"))

    assert group.token_remaining_kwh == pytest.approx(500.0, abs=0.01)


async def test_the_typed_amount_survives_a_restart(hass: HomeAssistant) -> None:
    """Angka yang sudah diketik tidak hilang kalau Home Assistant restart."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)

    await _set_number(hass, _entity_id(hass, "number", "topup_kwh"), 826.50)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.billing_groups[GROUP_ID].inputs["topup_kwh"] == 826.50


# --- pengaturan --------------------------------------------------------------


async def test_threshold_can_be_changed_from_the_dashboard(
    hass: HomeAssistant,
) -> None:
    """Ambang peringatan tersimpan ke konfigurasi, bukan cuma di memori."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)

    await _set_number(hass, _entity_id(hass, "number", "warning_threshold_days"), 10.0)

    assert entry.subentries[GROUP_ID].data[CONF_WARNING_THRESHOLD_DAYS] == 10.0
    assert entry.runtime_data.billing_groups[GROUP_ID].thresholds.warning_days == 10.0


async def test_out_of_order_thresholds_are_refused(hass: HomeAssistant) -> None:
    """Urutan yang tidak masuk akal ditolak, bukan diam-diam diurutkan sendiri."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)
    before = entry.subentries[GROUP_ID].data.get(CONF_CRITICAL_THRESHOLD_DAYS)

    # Kritis tidak boleh melewati Peringatan yang bawaannya 7 hari.
    with pytest.raises(HomeAssistantError):
        await _set_number(
            hass, _entity_id(hass, "number", "critical_threshold_days"), 20.0
        )

    assert entry.subentries[GROUP_ID].data.get(CONF_CRITICAL_THRESHOLD_DAYS) == before


async def test_changing_the_rate_adds_a_version_instead_of_overwriting(
    hass: HomeAssistant,
) -> None:
    """Kenaikan tarif tidak boleh menulis ulang biaya yang sudah tercatat.

    Riwayat tarif adalah jejak audit (spec K.7): tiap kenaikan menambah versi
    baru, sehingga biaya masa lalu tetap memakai tarif yang berlaku saat
    pemakaian itu terjadi.
    """
    from homeassistant.helpers import entity_registry as er

    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)

    rate_entity = er.async_get(hass).async_get_entity_id(
        "number", DOMAIN, f"{TARIFF_ID}_rate_rp_per_kwh"
    )
    assert rate_entity

    await _set_number(hass, rate_entity, 1699.53)

    data = entry.subentries[TARIFF_ID].data
    assert data[CONF_RATE_RP_PER_KWH] == 1699.53
    assert [
        version["rate_rp_per_kwh"] for version in data[CONF_RATE_HISTORY]
    ] == [1699.53]


async def test_the_tariff_gets_its_own_device(hass: HomeAssistant) -> None:
    """Tarif dipakai bersama banyak kelompok, jadi ia berdiri sendiri.

    Kalau ditempelkan ke perangkat kelompok tagihan, dua kelompok yang berbagi
    tarif akan punya dua entity yang diam-diam mengubah angka yang sama.
    """
    from homeassistant.helpers import device_registry as dr

    apply_states(hass, MCB_RUMAH)
    await _setup(hass)

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, TARIFF_ID)})
    assert device is not None
    assert device.model == "Tariff"


# --- dashboard ---------------------------------------------------------------


async def test_dashboard_offers_reset_behind_a_confirmation(
    hass: HomeAssistant,
) -> None:
    """Reset ada di dashboard, tapi selalu bertanya dulu.

    Reset sengaja tidak dibuat sebagai entity button: menekan entity button
    langsung menjalankan aksinya tanpa dialog, sementara reset menggantikan
    seluruh pengisian yang masih aktif dan tidak bisa dibatalkan.
    """
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)

    from custom_components.pln_prepaid_monitor.dashboard import build_dashboard

    def _walk(cards):
        for card in cards:
            yield card
            yield from _walk(card.get("cards", []))
            if nested := card.get("card"):
                yield nested
                yield from _walk(nested.get("cards", []))

    resets = [
        card
        for view in build_dashboard(hass, entry.runtime_data)["views"]
        for card in _walk(view_cards(view))
        if card.get("type") == "button"
        and card["tap_action"]["perform_action"].endswith("reset_token_ledger")
    ]

    assert len(resets) == 1
    assert "confirmation" in resets[0]["tap_action"]

    # Dan tidak ada entity button untuk reset - hanya tombol kartu.
    assert not [
        state
        for state in hass.states.async_all("button")
        if "reset" in state.entity_id
    ]


# --- mengisi token dengan nominal, bukan kWh ---------------------------------


async def test_topup_by_purchase_amount_converts_to_kwh(
    hass: HomeAssistant,
) -> None:
    """Isi nominalnya saja; kWh dihitung dari tarif yang berlaku."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)
    group = entry.runtime_data.billing_groups[GROUP_ID]

    await _set_number(hass, _entity_id(hass, "number", "topup_rp"), 1_000_000)
    await _press(hass, _entity_id(hass, "button", "record_topup"))

    # 1.000.000 / 1.444,70 = 692,19 kWh
    assert group.token_remaining_kwh == pytest.approx(692.19, abs=0.01)
    entry_row = group.ledger.state.entries[-1]
    assert entry_row["nominal_rp"] == 1_000_000


async def test_topup_by_kwh_fills_in_the_amount(hass: HomeAssistant) -> None:
    """Isi kWh-nya saja; nominalnya dihitung supaya riwayat tidak kosong."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)
    group = entry.runtime_data.billing_groups[GROUP_ID]

    await _set_number(hass, _entity_id(hass, "number", "topup_kwh"), 826.50)
    await _press(hass, _entity_id(hass, "button", "record_topup"))

    # 826,50 x 1.444,70 = 1.194.044,55
    assert group.ledger.state.entries[-1]["nominal_rp"] == pytest.approx(
        1_194_044.55, abs=0.01
    )


async def test_filling_in_both_uses_both_as_given(hass: HomeAssistant) -> None:
    """Struk menyebut keduanya; kalau user menyalin keduanya, jangan ditebak ulang.

    Ini kasus paling tepat: tidak ada satu pun angka yang perlu dikonversi, jadi
    tidak ada satu pun yang bisa meleset.
    """
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)
    group = entry.runtime_data.billing_groups[GROUP_ID]

    await _set_number(hass, _entity_id(hass, "number", "topup_kwh"), 826.50)
    await _set_number(hass, _entity_id(hass, "number", "topup_rp"), 1_000_000)
    await _press(hass, _entity_id(hass, "button", "record_topup"))

    row = group.ledger.state.entries[-1]
    assert row["kwh_credited"] == 826.50
    assert row["nominal_rp"] == 1_000_000


async def test_both_boxes_are_cleared_after_recording(hass: HomeAssistant) -> None:
    """Kalau salah satu tertinggal terisi, tekanan berikutnya jadi salah diam-diam."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass)

    await _set_number(hass, _entity_id(hass, "number", "topup_kwh"), 826.50)
    await _set_number(hass, _entity_id(hass, "number", "topup_rp"), 1_000_000)
    await _press(hass, _entity_id(hass, "button", "record_topup"))

    for key in ("topup_kwh", "topup_rp"):
        assert hass.states.get(_entity_id(hass, "number", key)).state == "0.0"


async def test_amount_without_a_tariff_is_refused(hass: HomeAssistant) -> None:
    """Tanpa tarif, nominal tidak bisa jadi kWh - ditolak, bukan ditebak."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, group_overrides={CONF_TARIFF_ID: None})
    group = entry.runtime_data.billing_groups[GROUP_ID]

    await _set_number(hass, _entity_id(hass, "number", "topup_rp"), 1_000_000)

    with pytest.raises(HomeAssistantError):
        await _press(hass, _entity_id(hass, "button", "record_topup"))

    assert group.ledger.state.entries == []


async def test_a_later_rate_change_does_not_rewrite_past_purchases(
    hass: HomeAssistant,
) -> None:
    """Harga pembelian yang sudah lewat tidak boleh ikut berubah saat tarif naik."""
    from homeassistant.helpers import entity_registry as er

    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)
    group = entry.runtime_data.billing_groups[GROUP_ID]

    await _set_number(hass, _entity_id(hass, "number", "topup_kwh"), 100.0)
    await _press(hass, _entity_id(hass, "button", "record_topup"))
    before = group.ledger.state.entries[-1]["nominal_rp"]

    rate_entity = er.async_get(hass).async_get_entity_id(
        "number", DOMAIN, f"{TARIFF_ID}_rate_rp_per_kwh"
    )
    await _set_number(hass, rate_entity, 2000.0)

    after = entry.runtime_data.billing_groups[GROUP_ID].ledger.state.entries[-1]
    assert after["nominal_rp"] == before
