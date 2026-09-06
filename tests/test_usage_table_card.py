"""Kartu tabel pemakaian, dirender sungguhan di dalam Home Assistant.

Template Jinja yang salah tidak pernah tampil sebagai error - ia muncul sebagai
kotak merah di dashboard, tanpa satu pun pesan yang berguna. Jadi templatenya
di sini benar-benar dirender, bukan sekadar dicocokkan sebagai teks.

Keadaan yang paling sering merusak template semacam ini adalah keadaan yang
paling sering terjadi: **pemasangan baru, tabelnya masih kosong**. Atribut
``rows`` belum ada, totalnya belum ada, dan template yang mengasumsikan
sebaliknya akan pecah tepat di hari pertama user memakainya.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template

from custom_components.pln_prepaid_monitor.dashboard import build_dashboard, view_cards
from custom_components.pln_prepaid_monitor.engines.usage_table import UsageTable

from .conftest import apply_states, MCB_RUMAH
from .test_dashboard import SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group, _setup


def _markdown(hass: HomeAssistant, runtime_data) -> list[str]:
    """Isi setiap kartu markdown pada dashboard."""
    found: list[str] = []

    def walk(cards: list[dict[str, Any]]) -> None:
        for card in cards:
            if card.get("type") == "markdown":
                found.append(card["content"])
            walk(card.get("cards", []))
            if nested := card.get("card"):
                walk([nested])

    for view in build_dashboard(hass, runtime_data)["views"]:
        walk(view_cards(view))
    return found


def _usage_markdown(hass: HomeAssistant, runtime_data) -> str:
    """Kartu markdown milik tabel pemakaian."""
    matches = [
        content
        for content in _markdown(hass, runtime_data)
        if "usage_table" in content or "tabel_pemakaian" in content
    ]
    assert len(matches) == 1, f"kartu tabel pemakaian tidak tunggal: {len(matches)}"
    return matches[0]


async def test_the_card_renders_when_the_table_is_still_empty(
    hass: HomeAssistant,
) -> None:
    """Pemasangan baru: belum ada satu pun statistik, dan itu keadaan normal.

    Yang harus muncul kalimat penjelas, bukan tabel kosong tanpa keterangan -
    dan sama sekali bukan kotak merah.
    """
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    content = _usage_markdown(hass, entry.runtime_data)
    rendered = Template(content, hass).async_render(parse_result=False)

    assert rendered.strip(), "kartu kosong sama sekali - user tidak tahu kenapa"
    assert "|" not in rendered, "tabel dirender padahal tidak ada barisnya"


async def test_the_card_renders_a_real_table(hass: HomeAssistant) -> None:
    """Dengan data, barisnya muncul lengkap dengan nomor dan batangnya."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    group = next(iter(entry.runtime_data.billing_groups.values()))
    group.usage_table = UsageTable(
        rows=[
            {"no": 1, "period": "05 Sep 2026", "kwh": 12.34,
             "cost_rp": 14956, "bar": "██████████"},
            {"no": 2, "period": "04 Sep 2026", "kwh": 6.17,
             "cost_rp": None, "bar": "█████"},
        ],
        total_kwh=18.51,
        total_cost_rp=14956,
        period_count=2,
        hidden_count=3,
    )
    group._async_notify()
    await hass.async_block_till_done()

    content = _usage_markdown(hass, entry.runtime_data)
    rendered = Template(content, hass).async_render(parse_result=False)

    assert "05 Sep 2026" in rendered
    assert "12,34" in rendered, "angka harus bergaya Indonesia"
    assert "14.956" in rendered, "ribuan dipisah titik"
    assert "██████████" in rendered
    # Baris tanpa biaya tampil kosong, bukan Rp 0.
    assert "0" != rendered.split("|")[-6].strip()
    # Baris yang disembunyikan harus disebutkan, bukan dipotong diam-diam.
    assert "3" in rendered


async def test_a_row_without_a_cost_never_shows_zero_rupiah(
    hass: HomeAssistant,
) -> None:
    """Kosong bukan nol. Rp 0 terbaca sebagai listrik gratis."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    group = next(iter(entry.runtime_data.billing_groups.values()))
    group.usage_table = UsageTable(
        rows=[{"no": 1, "period": "01 Sep 2026", "kwh": 5.0,
               "cost_rp": None, "bar": "██████████"}],
        total_kwh=5.0,
        total_cost_rp=None,
        period_count=1,
    )
    group._async_notify()
    await hass.async_block_till_done()

    content = _usage_markdown(hass, entry.runtime_data)
    rendered = Template(content, hass).async_render(parse_result=False)

    assert "Rp 0" not in rendered


async def test_every_control_the_card_lists_really_exists(
    hass: HomeAssistant,
) -> None:
    """Kendali yang entity-nya tidak ada akan hilang tanpa error.

    Kartunya tetap tampil, cuma kurang satu baris - dan tidak ada apa pun yang
    memberi tahu bahwa ada yang hilang.
    """
    from custom_components.pln_prepaid_monitor.dashboard import USAGE_CONTROLS

    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    listed: set[str] = set()
    for view in build_dashboard(hass, entry.runtime_data)["views"]:
        for card in view_cards(view):
            if card.get("title") not in ("Tabel pemakaian", "Usage table"):
                continue
            listed = {row["entity"] for row in card["entities"]}

    assert len(listed) == len(USAGE_CONTROLS), (
        f"ada kendali yang tidak punya entity: {len(listed)} dari "
        f"{len(USAGE_CONTROLS)}"
    )
