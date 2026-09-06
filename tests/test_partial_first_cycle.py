"""Siklus pertama sesudah pemasangan tidak boleh mengaku lebih panjang.

Pemilik memasang integrasi ini Sabtu 5 September siang, lalu Minggu pagi
melihat "Minggu ini 36,55 kWh" - lebih kecil daripada jumlah tiga hari terakhir
di tabel yang sama. Angkanya tidak mustahil, tidak ada error, dan tidak ada
satu pun petunjuk kenapa.

Sebabnya: penghitung siklus melaporkan ``last_reset`` di **batas siklus
resmi** - Senin 31 Agustus untuk minggu, 1 September untuk bulan, 1 Januari
untuk tahun - padahal angkanya baru mulai diukur Sabtu siang. Labelnya
berbohong tentang rentang yang dicakupnya.

Bukan cuma soal pembaca. Home Assistant memakai ``last_reset`` untuk
menafsirkan penurunan pada sensor ``state_class: total``, jadi tanggal yang
mengada-ada bisa merusak statistik jangka panjangnya sendiri.

Yang dikunci di sini: ``last_reset`` selalu menyebut saat pengukuran benar-
benar dimulai, dan siklus yang belum penuh ditandai apa adanya.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.pln_prepaid_monitor.engines.energy_calc import PeriodCounter
from custom_components.pln_prepaid_monitor.engines.period import CycleConfig

JAKARTA = ZoneInfo("Asia/Jakarta")

# Waktu pemasangan yang sesungguhnya, kata pemilik: Sabtu siang.
INSTALLED = datetime(2026, 9, 5, 12, 0, tzinfo=JAKARTA)
# Saat screenshot diambil: Minggu pagi.
LATER = datetime(2026, 9, 6, 9, 0, tzinfo=JAKARTA)

AT_INSTALL = 15_200.0
AT_LATER = 15_236.55


def _counter(period: str) -> PeriodCounter:
    """Penghitung yang baru dipasang di tengah siklus."""
    counter = PeriodCounter(period, CycleConfig())
    counter.sync(AT_INSTALL, INSTALLED)
    return counter


@pytest.mark.parametrize("period", ["week", "month", "year"])
def test_last_reset_names_when_measuring_really_started(period: str) -> None:
    """Bukan batas siklus resmi - itu tanggal yang tidak pernah kita lewati."""
    counter = _counter(period)

    assert counter.cycle_start_at == INSTALLED


@pytest.mark.parametrize("period", ["week", "month", "year"])
def test_a_partial_first_cycle_says_so(period: str) -> None:
    """Angkanya nyata, tapi rentangnya lebih pendek daripada namanya."""
    counter = _counter(period)

    assert counter.covers_full_cycle(LATER) is False


def test_the_value_itself_is_still_real() -> None:
    """Menandainya belum penuh bukan alasan menyembunyikan angkanya."""
    counter = _counter("month")
    counter.sync(AT_LATER, LATER)

    assert counter.value(AT_LATER) == pytest.approx(36.55)


def test_the_first_boundary_that_really_passes_makes_it_whole() -> None:
    """Sesudah batas siklus sungguhan terlewati, semuanya kembali normal."""
    counter = _counter("week")
    # Senin 7 September: batas minggu yang pertama benar-benar kita lewati.
    monday = datetime(2026, 9, 8, 10, 0, tzinfo=JAKARTA)

    rolled_over = counter.sync(AT_LATER, monday)

    assert rolled_over is True
    assert counter.cycle_start_at == datetime(2026, 9, 7, tzinfo=JAKARTA)
    assert counter.covers_full_cycle(monday) is True


def test_a_daily_counter_becomes_whole_the_very_next_day() -> None:
    """Harian pulih paling cepat - batasnya lewat tiap tengah malam."""
    counter = _counter("day")
    counter.sync(AT_LATER, LATER)

    assert counter.cycle_start_at == datetime(2026, 9, 6, tzinfo=JAKARTA)
    assert counter.covers_full_cycle(LATER) is True


def test_a_counter_installed_exactly_on_the_boundary_is_already_whole() -> None:
    """Dipasang tepat tengah malam berarti tidak ada yang terlewat."""
    counter = PeriodCounter("day", CycleConfig())
    midnight = datetime(2026, 9, 5, tzinfo=JAKARTA)
    counter.sync(AT_INSTALL, midnight)

    assert counter.cycle_start_at == midnight
    assert counter.covers_full_cycle(midnight + timedelta(hours=6)) is True


def test_restarting_home_assistant_does_not_move_the_start(hass_free=None) -> None:
    """State tersimpan menang; restart bukan pemasangan baru.

    Kalau tidak, tiap restart akan menggeser titik awal dan penghitungnya
    kembali ke nol - kehilangan seluruh pemakaian siklus itu tanpa jejak.
    """
    from custom_components.pln_prepaid_monitor.engines.energy_calc import (
        PeriodCounterState,
    )

    original = _counter("month")
    restored = PeriodCounter(
        "month",
        CycleConfig(),
        PeriodCounterState.from_dict(original.state.as_dict()),
    )
    restored.sync(AT_LATER, LATER)

    assert restored.cycle_start_at == INSTALLED
    assert restored.value(AT_LATER) == pytest.approx(36.55)
