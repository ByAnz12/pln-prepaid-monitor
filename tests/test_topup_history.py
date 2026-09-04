"""Kartu riwayat pengisian token.

Kartu ini satu-satunya yang memakai template Jinja. Template di dashboard tidak
punya pesan error yang berguna - kalau salah, user hanya melihat kartu kosong
atau tulisan merah. Jadi di sini template-nya benar-benar dirender, bukan
sekadar dicocokkan teksnya.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from custom_components.pln_prepaid_monitor.engines.token_engine import topup_log

from custom_components.pln_prepaid_monitor.dashboard import view_cards

from .conftest import apply_states, MCB_RUMAH
from .test_interactive import (
    GROUP_ID,
    _entity_id,
    _press,
    _set_number,
    _setup,
)


# --- penyusunan log ----------------------------------------------------------


def test_newest_first_and_numbered_from_one() -> None:
    """Nomor 1 adalah pengisian terakhir - itu yang dicari orang."""
    entries = [
        {"kind": "topup", "timestamp": "2026-01-01T10:00:00", "kwh_credited": 100.0},
        {"kind": "calibration", "timestamp": "2026-02-01T10:00:00"},
        {"kind": "topup", "timestamp": "2026-03-01T10:00:00", "kwh_credited": 200.0},
    ]

    log = topup_log(entries)

    assert [row["no"] for row in log] == [1, 2]
    assert [row["kwh"] for row in log] == [200.0, 100.0]
    # Kalibrasi bukan pengisian, jadi tidak ikut masuk.
    assert len(log) == 2


def test_limit_cuts_the_oldest_not_the_newest() -> None:
    """Membatasi jumlah baris tidak boleh menyembunyikan pengisian terbaru."""
    entries = [
        {"kind": "topup", "timestamp": f"2026-01-0{n}T10:00:00", "kwh_credited": n}
        for n in range(1, 6)
    ]

    log = topup_log(entries, limit=2)

    assert [row["kwh"] for row in log] == [5.0, 4.0]


def test_superseded_topups_are_shown_but_marked() -> None:
    """Menyembunyikannya membuat riwayat berbohong; jadi ditampilkan dan ditandai."""
    entries = [
        {
            "kind": "topup",
            "timestamp": "2026-01-01T10:00:00",
            "kwh_credited": 100.0,
            "superseded": True,
        },
        {"kind": "topup", "timestamp": "2026-02-01T10:00:00", "kwh_credited": 200.0},
    ]

    log = topup_log(entries)

    assert [row["superseded"] for row in log] == [False, True]


def test_broken_entries_do_not_break_the_card() -> None:
    """Satu entri rusak tidak boleh menjatuhkan seluruh riwayat."""
    entries = [
        {"kind": "topup", "timestamp": "2026-01-01T10:00:00", "kwh_credited": "abc"},
        {"kind": "topup", "timestamp": "2026-02-01T10:00:00", "kwh_credited": 200.0},
    ]

    log = topup_log(entries)

    assert [row["kwh"] for row in log] == [200.0, 0.0]


# --- kartunya di dashboard ---------------------------------------------------


def _history_markdown(hass: HomeAssistant, runtime_data) -> str:
    """Ambil isi template kartu riwayat dari dashboard yang dibuatkan."""
    from custom_components.pln_prepaid_monitor.dashboard import build_dashboard

    def _walk(cards):
        for card in cards:
            yield card
            yield from _walk(card.get("cards", []))
            if nested := card.get("card"):
                yield nested
                yield from _walk(nested.get("cards", []))

    contents = [
        card["content"]
        for view in build_dashboard(hass, runtime_data)["views"]
        for card in _walk(view_cards(view))
        if card.get("type") == "markdown" and "topup_log" in card.get("content", "")
    ]
    assert len(contents) == 1, "kartu riwayat harus ada tepat satu"
    return contents[0]


def _render(hass: HomeAssistant, content: str) -> str:
    from homeassistant.helpers.template import Template

    return Template(content, hass).async_render(parse_result=False)


async def test_the_template_actually_renders(hass: HomeAssistant) -> None:
    """Template-nya dirender sungguhan, bukan cuma dicek teksnya."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)

    await _set_number(hass, _entity_id(hass, "number", "topup_kwh"), 826.50)
    await _press(hass, _entity_id(hass, "button", "record_topup"))

    rendered = _render(hass, _history_markdown(hass, entry.runtime_data))

    assert "826.50" in rendered or "826,50" in rendered
    assert "| 1 |" in rendered


async def test_empty_history_says_so_instead_of_showing_an_empty_table(
    hass: HomeAssistant,
) -> None:
    """Tabel kosong tanpa penjelasan terlihat seperti kartu rusak."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)

    rendered = _render(hass, _history_markdown(hass, entry.runtime_data))

    assert "Belum ada pengisian" in rendered or "No top-up recorded" in rendered
    assert "|" not in rendered


async def test_the_row_count_box_changes_how_many_rows_render(
    hass: HomeAssistant,
) -> None:
    """Kotak "Tampilkan berapa baris" benar-benar mengubah tabelnya."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)
    amount = _entity_id(hass, "number", "topup_kwh")
    record = _entity_id(hass, "button", "record_topup")

    for value in (10.0, 20.0, 30.0):
        await _set_number(hass, amount, value)
        await _press(hass, record)

    content = _history_markdown(hass, entry.runtime_data)
    assert _render(hass, content).count("\n|") == 5  # header + pemisah + 3 baris

    await _set_number(hass, _entity_id(hass, "number", "history_rows"), 2)

    assert _render(hass, content).count("\n|") == 4  # header + pemisah + 2 baris


@pytest.mark.parametrize("rows", [1, 5, 20, 50])
async def test_any_row_count_is_accepted(hass: HomeAssistant, rows: int) -> None:
    """5/10/20 bukan pilihan tetap - angka berapa pun boleh."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass)

    await _set_number(hass, _entity_id(hass, "number", "history_rows"), rows)

    state = hass.states.get(_entity_id(hass, "number", "history_rows"))
    assert float(state.state) == rows
    assert GROUP_ID  # dipakai lewat _entity_id
