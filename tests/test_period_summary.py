"""Rincian per periode: rata-rata, beberapa hari lalu, beberapa bulan lalu."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.pln_prepaid_monitor.const import CONF_DETAIL_ROWS
from custom_components.pln_prepaid_monitor.dashboard import view_cards
from custom_components.pln_prepaid_monitor.engines.period_summary import (
    DEFAULT_ROWS,
    ROW_KEYS,
    selected_rows,
    summarise,
)

from .conftest import apply_states, MCB_RUMAH
from .test_interactive import GROUP_ID, _setup

NOW = datetime(2026, 9, 5, 14, 30, tzinfo=dt_util.UTC)


def _days(values: list[float], end: datetime = NOW) -> list[tuple[datetime, float]]:
    """Data harian yang berakhir pada hari ``end``, urut dari yang paling lama."""
    start = end - timedelta(days=len(values) - 1)
    return [(start + timedelta(days=n), value) for n, value in enumerate(values)]


def _months(values: list[float]) -> list[tuple[datetime, float]]:
    """Data bulanan yang berakhir pada bulan ``NOW``."""
    out = []
    year, month = NOW.year, NOW.month - len(values) + 1
    for value in values:
        while month < 1:
            month += 12
            year -= 1
        out.append((datetime(year, month, 1, tzinfo=dt_util.UTC), value))
        month += 1
    return out


# --- perhitungan -------------------------------------------------------------


def test_yesterday_is_yesterday_not_today() -> None:
    """Hari yang sedang berjalan tidak boleh terhitung sebagai "kemarin"."""
    summary = summarise( daily=_days([10.0, 20.0, 30.0, 40.0]), monthly=[], now=NOW
    )

    # 40.0 adalah hari ini, jadi kemarin = 30.0.
    assert summary["prev_day_1"] == 30.0
    assert summary["prev_day_2"] == 20.0
    assert summary["prev_day_3"] == 10.0


def test_the_running_period_never_drags_the_average_down() -> None:
    """Hari yang baru berjalan sejam akan menyeret rata-rata turun tanpa alasan.

    Kesalahan seperti ini tidak terlihat salah - angkanya tetap "masuk akal",
    hanya diam-diam meleset. Karena itu dikunci di sini.
    """
    summary = summarise( daily=_days([10.0, 10.0, 10.0, 0.5]), monthly=[], now=NOW
    )

    assert summary["avg_daily"] == 10.0


def test_last_month_is_last_month_not_this_one() -> None:
    """Bulan berjalan juga dikecualikan, dengan alasan yang sama."""
    summary = summarise( daily=[], monthly=_months([100.0, 200.0, 300.0, 5.0]), now=NOW
    )

    assert summary["prev_month_1"] == 300.0
    assert summary["prev_month_2"] == 200.0
    assert summary["prev_month_3"] == 100.0
    assert summary["avg_monthly"] == 200.0


def test_missing_data_becomes_none_not_zero() -> None:
    """Nol berarti "tidak ada pemakaian"; kosong berarti "belum ada datanya"."""
    summary = summarise( daily=[], monthly=[], now=NOW)

    assert set(summary) == set(ROW_KEYS)
    assert all(summary[key] is None for key in summary)


# --- pilihan baris -----------------------------------------------------------


def test_no_choice_means_the_sensible_default() -> None:
    """Belum pernah diatur bukan berarti kartunya kosong."""
    assert selected_rows(None) == list(DEFAULT_ROWS)
    assert selected_rows([]) == list(DEFAULT_ROWS)


def test_row_order_never_follows_the_order_they_were_ticked() -> None:
    """Urutan yang berbeda-beda antar kelompok tagihan lebih sulit dibaca."""
    assert selected_rows(["prev_day_1", "avg_hourly", "this_day"]) == [
        "avg_hourly",
        "this_day",
        "prev_day_1",
    ]


def test_unknown_rows_are_dropped_quietly() -> None:
    """Konfigurasi lama tidak boleh menjatuhkan kartunya."""
    assert selected_rows(["this_day", "sesuatu_yang_tidak_ada"]) == ["this_day"]


# --- kartunya ----------------------------------------------------------------


def _summary_cards(hass: HomeAssistant, runtime_data) -> list[str]:
    from custom_components.pln_prepaid_monitor.dashboard import build_dashboard

    return [
        card["content"]
        for view in build_dashboard(hass, runtime_data)["views"]
        for card in view_cards(view)
        if card.get("type") == "markdown"
        and "period_summary" in card.get("content", "")
    ]


def _columns(rendered: str) -> int:
    """Jumlah kolom pada baris judul tabel yang dirender."""
    header = next(l for l in rendered.splitlines() if l.strip().startswith("|"))
    return header.count("|")


def _render(hass: HomeAssistant, content: str) -> str:
    from homeassistant.helpers.template import Template

    return Template(content, hass).async_render(parse_result=False)


async def test_usage_and_cost_share_one_card(hass: HomeAssistant) -> None:
    """Satu kartu, dua kolom - bukan dua kartu terpisah.

    Dulu terpisah, dan itu memaksa mata bolak-balik untuk menjawab pertanyaan
    yang sebenarnya satu: "bulan kemarin berapa kWh, dan berapa rupiahnya?".
    """
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)

    contents = _summary_cards(hass, entry.runtime_data)
    assert len(contents) == 1, "pemakaian dan biaya harus jadi satu kartu"

    rendered = _render(hass, contents[0])
    assert "Hari ini" in rendered or "Today" in rendered
    # Tiga kolom: label, kWh, dan mata uang.
    assert _columns(rendered) == 4


async def test_rows_without_data_show_a_dash_not_a_broken_number(
    hass: HomeAssistant,
) -> None:
    """Instalasi baru belum punya data bulan lalu; itu harus terlihat wajar."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)

    rendered = _render(hass, _summary_cards(hass, entry.runtime_data)[0])

    assert "| - |" in rendered
    assert "None" not in rendered


@pytest.mark.parametrize(
    "rows", [["this_day"], ["avg_daily", "prev_month_1"], list(ROW_KEYS)]
)
async def test_the_checklist_decides_which_rows_appear(
    hass: HomeAssistant, rows: list[str]
) -> None:
    """Baris yang tampil persis yang dicentang, tidak lebih dan tidak kurang."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, group_overrides={CONF_DETAIL_ROWS: rows})

    rendered = _render(hass, _summary_cards(hass, entry.runtime_data)[0])

    assert rendered.count("\n|") == len(rows) + 2  # header + pemisah
    assert entry.runtime_data.billing_groups[GROUP_ID]


async def test_both_columns_say_what_their_numbers_are(
    hass: HomeAssistant,
) -> None:
    """Angka telanjang tidak memberi tahu apa-apa - "230" itu rupiah atau kWh?"""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)

    rendered = _render(hass, _summary_cards(hass, entry.runtime_data)[0])

    assert "kWh" in rendered
    assert "Rp" in rendered


async def test_a_group_without_a_tariff_gets_only_the_kwh_column(
    hass: HomeAssistant,
) -> None:
    """Tanpa tarif tidak ada angka biaya, jadi kolomnya tidak dibuat kosong."""
    from custom_components.pln_prepaid_monitor.const import CONF_TARIFF_ID

    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, group_overrides={CONF_TARIFF_ID: None})

    rendered = _render(hass, _summary_cards(hass, entry.runtime_data)[0])

    assert "kWh" in rendered
    assert "Rp" not in rendered
    # Dua kolom, bukan tiga.
    assert _columns(rendered) == 3


def test_the_hourly_average_always_agrees_with_the_daily_one() -> None:
    """Dua angka rata-rata di kartu yang sama tidak boleh saling membantah.

    Kalau dihitung sendiri-sendiri dari jendela data yang berbeda, "rata-rata
    per jam" dikali 24 tidak sama dengan "rata-rata harian" - dan pembaca kartu
    tidak punya cara tahu kenapa. Diturunkan dari yang harian, keduanya selalu
    rukun.
    """
    summary = summarise(daily=_days([12.0, 24.0, 0.5]), monthly=[], now=NOW)

    assert summary["avg_daily"] == 18.0
    assert summary["avg_hourly"] == 0.75
    assert summary["avg_hourly"] * 24 == summary["avg_daily"]
