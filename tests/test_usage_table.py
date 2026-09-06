"""Tabel riwayat: penyaringan, pengelompokan, dan pengurutannya.

Semuanya murni aritmetika, jadi diuji tanpa Home Assistant.

Satu aturan diwarisi langsung dari D-055 dan dijaga ulang di sini: kWh dan
Rupiah pada satu baris harus berasal dari periode yang **sama**. Tabel ini
menyandingkan dua statistik yang berbeda persis seperti kartu rincian, jadi ia
mewarisi jebakan yang sama - dan kalau dipasangkan per posisi, hasilnya tetap
terlihat masuk akal.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.pln_prepaid_monitor.engines.usage_table import (
    BAR_WIDTH,
    DIRECTION_ASC,
    DIRECTION_DESC,
    GRAIN_DAY,
    GRAIN_MONTH,
    GRAIN_YEAR,
    MAX_ROWS_LIMIT,
    SORT_COST,
    SORT_KWH,
    SORT_TIME,
    UsageQuery,
    bar,
    build_table,
    clamp_view,
    finer_grains,
    range_bounds,
)

JAKARTA = ZoneInfo("Asia/Jakarta")
TARIF = 1212.0
BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def _label(key: tuple[int, ...], grain: str) -> str:
    """Pelabel sederhana, cukup untuk membedakan baris di dalam test."""
    if grain == GRAIN_YEAR:
        return str(key[0])
    if grain == GRAIN_MONTH:
        return f"{BULAN[key[1] - 1]} {key[0]}"
    return f"{key[2]:02d} {BULAN[key[1] - 1]} {key[0]}"


def _day(day: int, month: int = 9, year: int = 2026) -> datetime:
    return datetime(year, month, day, tzinfo=JAKARTA)


def _table(energy, cost=None, **kwargs):
    query = UsageQuery(**{"scope": GRAIN_MONTH, "view": GRAIN_DAY, **kwargs})
    return build_table(query=query, energy=energy, cost=cost, labeller=_label)


# --- tampilan tidak boleh lebih kasar dari jenis waktu -----------------------


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (GRAIN_DAY, [GRAIN_DAY]),
        (GRAIN_MONTH, [GRAIN_DAY, GRAIN_MONTH]),
        (GRAIN_YEAR, [GRAIN_DAY, GRAIN_MONTH, GRAIN_YEAR]),
    ],
)
def test_the_view_choices_narrow_with_the_scope(scope, expected) -> None:
    """Rentang dalam satuan hari tidak bisa ditampilkan per tahun."""
    assert finer_grains(scope) == expected


def test_a_view_that_is_too_coarse_is_pulled_back() -> None:
    """Pilihan lama yang jadi tidak sah dikoreksi, bukan dibiarkan meledak."""
    assert clamp_view(GRAIN_DAY, GRAIN_YEAR) == GRAIN_DAY
    assert clamp_view(GRAIN_YEAR, GRAIN_MONTH) == GRAIN_MONTH


# --- rentang -----------------------------------------------------------------


def test_a_month_range_covers_whole_months() -> None:
    """"Februari sampai Agustus" berarti seluruh Agustus, bukan sampai tanggal 1."""
    assert range_bounds(GRAIN_MONTH, date(2026, 2, 15), date(2026, 8, 3)) == (
        date(2026, 2, 1),
        date(2026, 8, 31),
    )


def test_a_month_range_ending_in_december_does_not_overflow() -> None:
    """Desember + 1 bulan adalah Januari tahun berikutnya."""
    assert range_bounds(GRAIN_MONTH, date(2026, 12, 5), date(2026, 12, 20))[1] == date(
        2026, 12, 31
    )


def test_a_year_range_covers_whole_years() -> None:
    assert range_bounds(GRAIN_YEAR, date(2025, 6, 1), date(2026, 2, 1)) == (
        date(2025, 1, 1),
        date(2026, 12, 31),
    )


def test_a_backwards_range_is_swapped_not_rejected() -> None:
    """User yang menukar "dari" dan "sampai" tetap mendapat tabel, bukan kosong."""
    assert range_bounds(GRAIN_DAY, date(2026, 9, 10), date(2026, 9, 1)) == (
        date(2026, 9, 1),
        date(2026, 9, 10),
    )


# --- pengelompokan dan pemasangan --------------------------------------------


def test_days_roll_up_into_months_when_the_view_says_so() -> None:
    """Jenis waktu tahun + tampilan bulan: data harian dijumlahkan per bulan."""
    energy = [_pair(_day(d, month=8), 1.0) for d in (1, 2, 3)] + [
        _pair(_day(d, month=9), 2.0) for d in (1, 2)
    ]

    table = _table(energy, scope=GRAIN_YEAR, view=GRAIN_MONTH, sort=SORT_TIME,
                   direction=DIRECTION_ASC)

    assert [row["period"] for row in table.rows] == ["Agu 2026", "Sep 2026"]
    assert [row["kwh"] for row in table.rows] == [3.0, 4.0]


def test_a_gap_in_the_cost_data_leaves_that_row_empty() -> None:
    """Warisan D-055. Kosong itu jujur; angka periode lain tidak."""
    energy = [_pair(_day(d), float(d)) for d in (1, 2, 3)]
    cost = [_pair(_day(d), float(d) * TARIF) for d in (1, 3)]

    table = _table(energy, cost, sort=SORT_TIME, direction=DIRECTION_ASC)

    for row in table.rows:
        if row["cost_rp"] is None:
            continue
        assert round(row["cost_rp"] / row["kwh"]) == TARIF


def test_every_row_that_has_both_sides_matches_the_tariff() -> None:
    """Ukuran yang sama dengan D-055, dipasang ulang di tabel baru."""
    energy = [_pair(_day(d), float(d) * 3) for d in range(1, 8)]
    cost = [_pair(_day(d), float(d) * 3 * TARIF) for d in range(1, 8)]

    table = _table(energy, cost, max_rows=MAX_ROWS_LIMIT)

    assert table.rows
    for row in table.rows:
        assert round(row["cost_rp"] / row["kwh"]) == TARIF
    assert round(table.total_cost_rp / table.total_kwh) == TARIF


# --- pengurutan --------------------------------------------------------------


def test_sorting_by_usage_ignores_the_calendar() -> None:
    energy = [_pair(_day(1), 5.0), _pair(_day(2), 30.0), _pair(_day(3), 12.0)]

    table = _table(energy, sort=SORT_KWH, direction=DIRECTION_DESC)

    assert [row["kwh"] for row in table.rows] == [30.0, 12.0, 5.0]
    # Nomor selalu mengikuti urutan tampil, bukan urutan tanggal.
    assert [row["no"] for row in table.rows] == [1, 2, 3]


def test_sorting_by_time_ascending_is_oldest_first() -> None:
    energy = [_pair(_day(3), 1.0), _pair(_day(1), 1.0), _pair(_day(2), 1.0)]

    table = _table(energy, sort=SORT_TIME, direction=DIRECTION_ASC)

    assert [row["period"] for row in table.rows] == [
        "01 Sep 2026",
        "02 Sep 2026",
        "03 Sep 2026",
    ]


def test_rows_without_a_cost_sink_to_the_bottom_when_sorting_by_cost() -> None:
    """Kosong bukan nol. Menaruhnya di puncak daftar "terbesar" menyesatkan."""
    energy = [_pair(_day(1), 10.0), _pair(_day(2), 20.0), _pair(_day(3), 30.0)]
    cost = [_pair(_day(1), 10.0 * TARIF), _pair(_day(2), 20.0 * TARIF)]

    table = _table(energy, cost, sort=SORT_COST, direction=DIRECTION_DESC)

    assert table.rows[-1]["cost_rp"] is None


# --- pagar panjang tabel -----------------------------------------------------


def test_the_row_cap_reports_what_it_hid() -> None:
    """Tabel yang dipotong diam-diam terbaca seperti data yang hilang."""
    energy = [_pair(_day(d), 1.0) for d in range(1, 11)]

    table = _table(energy, max_rows=3)

    assert len(table.rows) == 3
    assert table.period_count == 10
    assert table.hidden_count == 7


def test_the_totals_cover_the_whole_range_not_just_what_is_shown() -> None:
    """Total yang cuma menjumlahkan baris tampil menjawab pertanyaan yang salah."""
    energy = [_pair(_day(d), 2.0) for d in range(1, 11)]

    table = _table(energy, max_rows=3)

    assert table.total_kwh == 20.0


@pytest.mark.parametrize("asked", [0, -5, 1000])
def test_an_absurd_row_cap_is_brought_back_into_range(asked) -> None:
    energy = [_pair(_day(d), 1.0) for d in range(1, 6)]

    table = _table(energy, max_rows=asked)

    assert 1 <= len(table.rows) <= MAX_ROWS_LIMIT


def test_an_empty_range_says_so_instead_of_pretending() -> None:
    table = _table([])

    assert table.empty
    assert table.rows == []
    assert table.total_cost_rp is None


# --- batang perbandingan -----------------------------------------------------


def test_the_biggest_row_fills_the_bar() -> None:
    assert bar(10.0, 10.0) == "█" * BAR_WIDTH


def test_a_tiny_but_real_value_still_draws_something() -> None:
    """Batang kosong pada baris yang punya angka terbaca seperti data hilang."""
    assert bar(0.01, 1000.0) == "█"


def test_nothing_used_draws_nothing() -> None:
    assert bar(0.0, 10.0) == ""


def test_the_bar_scales_to_the_rows_on_screen() -> None:
    """Puncaknya diambil dari baris yang tampil, bukan dari seluruh rentang.

    Kalau diambil dari seluruh rentang, halaman yang isinya hari-hari kecil
    semuanya akan menampilkan batang yang nyaris tak terlihat.
    """
    energy = [_pair(_day(1), 100.0), _pair(_day(2), 4.0), _pair(_day(3), 2.0)]

    table = _table(energy, sort=SORT_KWH, direction=DIRECTION_ASC, max_rows=2)

    assert table.rows[0]["kwh"] == 2.0
    assert table.rows[-1]["bar"] == "█" * BAR_WIDTH


def _pair(moment: datetime, value: float) -> tuple[datetime, float]:
    """Satu titik statistik."""
    return (moment, value)
