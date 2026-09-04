"""Usulan harga per kWh dari pembelian token, dan template pengisian.

Harga per kWh adalah angka yang user tetapkan sendiri, dan seluruh biaya
dihitung darinya. Karena itu ia tidak pernah berubah tanpa persetujuan - yang
diuji di sini terutama adalah bagian "tidak pernah"-nya.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.pln_prepaid_monitor.const import (
    CONF_RATE_HISTORY,
    CONF_RATE_RP_PER_KWH,
    CONF_TOKEN_PRESETS,
    DOMAIN,
    SERVICE_RESOLVE_RATE_CHANGE,
)
from custom_components.pln_prepaid_monitor.dashboard import view_cards

from .conftest import apply_states, MCB_RUMAH
from .test_interactive import (
    GROUP_ID,
    TARIFF_ID,
    _entity_id,
    _press,
    _set_number,
    _setup,
)

# Tarif awal kelompok uji.
RATE = 1444.70


async def _topup(hass: HomeAssistant, kwh: float, nominal: float) -> None:
    """Catat pengisian lewat dashboard, dengan kedua angka terisi."""
    await _set_number(hass, _entity_id(hass, "number", "topup_kwh"), kwh)
    await _set_number(hass, _entity_id(hass, "number", "topup_rp"), nominal)
    await _press(hass, _entity_id(hass, "button", "record_topup"))


async def _decide(hass: HomeAssistant, apply: bool) -> None:
    from homeassistant.helpers import device_registry as dr

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, GROUP_ID)})
    await hass.services.async_call(
        DOMAIN,
        SERVICE_RESOLVE_RATE_CHANGE,
        {"device_id": [device.id], "apply": apply},
        blocking=True,
    )
    await hass.async_block_till_done()


# --- usulan harga ------------------------------------------------------------


async def test_a_receipt_produces_a_proposal_not_a_change(
    hass: HomeAssistant,
) -> None:
    """Harga tidak boleh berubah sendiri, sekalipun hitungannya benar."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)
    group = entry.runtime_data.billing_groups[GROUP_ID]

    # Template 1 milik user: 825 kWh seharga Rp 1.002.500 -> Rp 1.215,15/kWh
    await _topup(hass, 825.0, 1_002_500)

    assert group.pending_rate is not None
    assert group.pending_rate["to_rate"] == pytest.approx(1215.15, abs=0.01)
    # Yang penting: tarifnya belum berubah.
    assert entry.subentries[TARIFF_ID].data[CONF_RATE_RP_PER_KWH] == RATE


async def test_saying_yes_updates_the_rate_and_keeps_the_old_version(
    hass: HomeAssistant,
) -> None:
    """Harga baru dipakai, harga lama tetap tercatat sebagai versi sebelumnya."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)

    await _topup(hass, 825.0, 1_002_500)
    await _decide(hass, apply=True)

    data = entry.subentries[TARIFF_ID].data
    assert data[CONF_RATE_RP_PER_KWH] == pytest.approx(1215.15, abs=0.01)
    assert [v["rate_rp_per_kwh"] for v in data[CONF_RATE_HISTORY]] == [
        pytest.approx(1215.15, abs=0.01)
    ]
    assert entry.runtime_data.billing_groups[GROUP_ID].pending_rate is None


async def test_saying_no_leaves_everything_alone(hass: HomeAssistant) -> None:
    """Menolak berarti benar-benar tidak ada yang berubah."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)

    await _topup(hass, 825.0, 1_002_500)
    await _decide(hass, apply=False)

    assert entry.subentries[TARIFF_ID].data[CONF_RATE_RP_PER_KWH] == RATE
    assert entry.runtime_data.billing_groups[GROUP_ID].pending_rate is None


async def test_rounding_noise_does_not_ask(hass: HomeAssistant) -> None:
    """Selisih sekecil pembulatan struk tidak layak jadi pertanyaan."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)
    group = entry.runtime_data.billing_groups[GROUP_ID]

    # 100 kWh x tarif berjalan, dibulatkan - selisihnya jauh di bawah ambang.
    await _topup(hass, 100.0, round(100 * RATE))

    assert group.pending_rate is None


async def test_kwh_only_never_proposes(hass: HomeAssistant) -> None:
    """Nominal hasil hitungan sendiri tidak boleh jadi dasar mengubah harga.

    Kalau user hanya mengisi kWh, nominalnya kita hitung dari tarif yang
    berlaku. Memakai angka itu untuk mengusulkan tarif baru berarti sistem
    mengusulkan angkanya sendiri kembali - lingkaran yang tidak ada gunanya.
    """
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)
    group = entry.runtime_data.billing_groups[GROUP_ID]

    await _set_number(hass, _entity_id(hass, "number", "topup_kwh"), 826.50)
    await _press(hass, _entity_id(hass, "button", "record_topup"))

    assert group.pending_rate is None


async def test_a_wild_change_is_flagged_but_still_offered(
    hass: HomeAssistant,
) -> None:
    """Salah ketik ditandai, bukan ditolak diam-diam - user tetap yang menilai."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)
    group = entry.runtime_data.billing_groups[GROUP_ID]

    # 82,5 kWh (bukan 825) seharga sejuta -> harganya melonjak 8x.
    await _topup(hass, 82.5, 1_002_500)

    assert group.pending_rate["implausible"] is True


async def test_deciding_without_a_proposal_is_refused(hass: HomeAssistant) -> None:
    """Menekan tombol keputusan saat tidak ada yang diputuskan harus bersuara."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass)

    with pytest.raises(HomeAssistantError):
        await _decide(hass, apply=True)


async def test_the_proposal_survives_a_restart(hass: HomeAssistant) -> None:
    """Pertanyaan yang belum dijawab tidak boleh hilang karena restart."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)

    await _topup(hass, 825.0, 1_002_500)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.billing_groups[GROUP_ID].pending_rate is not None


async def test_the_question_only_shows_when_there_is_one(
    hass: HomeAssistant,
) -> None:
    """Kartunya bersyarat, jadi tidak mengganggu saat tidak ada pertanyaan."""
    from custom_components.pln_prepaid_monitor.dashboard import (
        build_dashboard,
        collect_views,
    )

    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)

    def _walk(cards):
        for card in cards:
            yield card
            yield from _walk(card.get("cards", []))
            if nested := card.get("card"):
                yield nested
                yield from _walk(nested.get("cards", []))

    # entity_id mengikuti bahasa Home Assistant, jadi dicocokkan lewat peran
    # entity-nya - bukan lewat potongan nama, yang berubah antar bahasa.
    pending = collect_views(hass, entry.runtime_data)[0].entity("rate_change_pending")
    conditionals = [
        card
        for page in build_dashboard(hass, entry.runtime_data)["views"]
        for card in _walk(view_cards(page))
        if card.get("type") == "conditional"
        and card["conditions"][0]["entity"] == pending
    ]
    assert len(conditionals) == 1

    buttons = [
        card
        for card in _walk([conditionals[0]["card"]])
        if card.get("type") == "button"
    ]
    assert len(buttons) == 2
    # Keduanya mengubah harga, jadi keduanya wajib bertanya dulu.
    assert all("confirmation" in b["tap_action"] for b in buttons)


# --- template pengisian ------------------------------------------------------


async def test_saving_a_template_from_what_is_typed(hass: HomeAssistant) -> None:
    """Cara membuat template: ketik angkanya, lalu tekan simpan."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)

    await _set_number(hass, _entity_id(hass, "number", "topup_kwh"), 825.0)
    await _set_number(hass, _entity_id(hass, "number", "topup_rp"), 1_002_500)
    await _press(hass, _entity_id(hass, "button", "save_template"))

    assert entry.subentries[GROUP_ID].data[CONF_TOKEN_PRESETS] == [
        {"kwh": 825.0, "nominal_rp": 1_002_500}
    ]


async def test_more_than_one_template_can_be_kept(hass: HomeAssistant) -> None:
    """Dua template berdampingan, sesuai contoh user."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)

    for kwh, nominal in ((825.0, 1_002_500), (425.0, 503_000)):
        await _set_number(hass, _entity_id(hass, "number", "topup_kwh"), kwh)
        await _set_number(hass, _entity_id(hass, "number", "topup_rp"), nominal)
        await _press(hass, _entity_id(hass, "button", "save_template"))

    saved = entry.subentries[GROUP_ID].data[CONF_TOKEN_PRESETS]
    assert [item["kwh"] for item in saved] == [825.0, 425.0]
    assert [item["nominal_rp"] for item in saved] == [1_002_500, 503_000]


async def test_a_template_needs_both_figures(hass: HomeAssistant) -> None:
    """Template tanpa salah satu angka tidak ada gunanya sebagai tombol."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass)

    await _set_number(hass, _entity_id(hass, "number", "topup_kwh"), 825.0)

    with pytest.raises(HomeAssistantError):
        await _press(hass, _entity_id(hass, "button", "save_template"))


async def test_the_same_template_twice_is_refused(hass: HomeAssistant) -> None:
    """Dua tombol dengan angka identik hanya membingungkan."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass)

    for _ in range(2):
        await _set_number(hass, _entity_id(hass, "number", "topup_kwh"), 825.0)
        await _set_number(hass, _entity_id(hass, "number", "topup_rp"), 1_002_500)
        if _ == 0:
            await _press(hass, _entity_id(hass, "button", "save_template"))
            continue
        with pytest.raises(HomeAssistantError):
            await _press(hass, _entity_id(hass, "button", "save_template"))


async def test_saved_templates_become_buttons(hass: HomeAssistant) -> None:
    """Yang disimpan langsung bisa dipakai sekali klik."""
    from custom_components.pln_prepaid_monitor.dashboard import build_dashboard

    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)

    await _set_number(hass, _entity_id(hass, "number", "topup_kwh"), 825.0)
    await _set_number(hass, _entity_id(hass, "number", "topup_rp"), 1_002_500)
    await _press(hass, _entity_id(hass, "button", "save_template"))

    def _walk(cards):
        for card in cards:
            yield card
            yield from _walk(card.get("cards", []))
            if nested := card.get("card"):
                yield nested
                yield from _walk(nested.get("cards", []))

    names = [
        card["name"]
        for page in build_dashboard(hass, entry.runtime_data)["views"]
        for card in _walk(view_cards(page))
        if card.get("type") == "button"
        and card["tap_action"]["perform_action"].endswith("add_token_topup")
    ]
    assert "Rp 1.002.500 (825,00 kWh)" in names
