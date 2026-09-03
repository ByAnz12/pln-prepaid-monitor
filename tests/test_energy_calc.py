"""Test penjumlahan grup dan penghitung periode - tanpa Home Assistant."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.pln_prepaid_monitor.engines.energy_calc import (
    GroupTotal,
    GroupTotalState,
    PeriodCounter,
    PeriodCounterState,
)
from custom_components.pln_prepaid_monitor.engines.period import (
    CycleConfig,
    CyclePeriod,
)

JAKARTA = ZoneInfo("Asia/Jakarta")


def _at(text: str) -> datetime:
    """Waktu lokal Jakarta dari string."""
    return datetime.fromisoformat(text).replace(tzinfo=JAKARTA)


def test_group_total_starts_at_sum_of_meter_readings() -> None:
    """Nilai awal grup = jumlah pembacaan meteran, supaya bisa dicocokkan."""
    group = GroupTotal()
    total = group.update({"rumah": 15498.27, "toko": 15114.43})
    assert total == pytest.approx(30612.70)


def test_group_total_accumulates_member_deltas() -> None:
    """Kenaikan tiap anggota dijumlahkan ke total grup."""
    group = GroupTotal()
    group.update({"rumah": 100.0, "toko": 200.0})
    total = group.update({"rumah": 105.0, "toko": 210.0})
    assert total == pytest.approx(315.0)


def test_new_member_history_is_not_counted_as_usage() -> None:
    """Anggota baru dengan angka meteran besar tidak jadi lonjakan pemakaian.

    Ini alasan total grup dihitung dari selisih, bukan dari menjumlahkan angka
    mentah setiap saat.
    """
    group = GroupTotal()
    group.update({"rumah": 100.0})
    # MCB TOKO baru dimasukkan ke grup, angkanya sudah 15.114 kWh.
    total = group.update({"rumah": 100.0, "toko": 15114.43})
    assert total == pytest.approx(100.0)

    # Mulai sekarang barulah pemakaiannya ikut terhitung.
    total = group.update({"rumah": 101.0, "toko": 15116.43})
    assert total == pytest.approx(103.0)


def test_member_that_goes_offline_simply_stops_contributing() -> None:
    """Anggota yang hilang tidak membuat total grup turun."""
    group = GroupTotal()
    group.update({"rumah": 100.0, "toko": 200.0})
    total = group.update({"rumah": 105.0, "toko": None})
    assert total == pytest.approx(305.0)

    # Begitu kembali, hanya selisih sejak pembacaan terakhirnya yang dihitung.
    total = group.update({"rumah": 105.0, "toko": 203.0})
    assert total == pytest.approx(308.0)


def test_removed_member_is_forgotten() -> None:
    """Anggota yang dikeluarkan dari grup dilupakan titik acuannya."""
    group = GroupTotal()
    group.update({"rumah": 100.0, "toko": 200.0})
    group.update({"rumah": 105.0})
    assert "toko" not in group.state.member_last


def test_group_total_returns_none_before_any_data() -> None:
    """Tanpa data sama sekali, total grup belum punya nilai."""
    group = GroupTotal()
    assert group.update({"rumah": None}) is None


def test_group_total_survives_restart() -> None:
    """Total grup dan titik acuan anggota bertahan lintas restart."""
    group = GroupTotal()
    group.update({"rumah": 100.0, "toko": 200.0})
    group.update({"rumah": 105.0, "toko": 210.0})

    restored = GroupTotal(GroupTotalState.from_dict(group.state.as_dict()))
    total = restored.update({"rumah": 106.0, "toko": 210.0})
    assert total == pytest.approx(316.0)


def test_period_counter_measures_usage_since_cycle_start() -> None:
    """Penghitung periode = total sekarang dikurangi total di awal siklus."""
    counter = PeriodCounter(CyclePeriod.DAY, CycleConfig())
    counter.sync(100.0, _at("2026-09-03 08:00:00"))
    assert counter.value(100.0) == pytest.approx(0.0)

    counter.sync(104.5, _at("2026-09-03 20:00:00"))
    assert counter.value(104.5) == pytest.approx(4.5)


def test_period_counter_resets_at_cycle_boundary() -> None:
    """Lewat tengah malam, penghitung harian mulai lagi dari nol."""
    counter = PeriodCounter(CyclePeriod.DAY, CycleConfig())
    counter.sync(100.0, _at("2026-09-03 08:00:00"))
    counter.sync(110.0, _at("2026-09-03 23:59:00"))
    assert counter.value(110.0) == pytest.approx(10.0)

    rolled = counter.sync(110.0, _at("2026-09-04 00:00:01"))
    assert rolled is True
    assert counter.value(110.0) == pytest.approx(0.0)
    assert counter.cycle_start_at == _at("2026-09-04 00:00:00")

    counter.sync(112.0, _at("2026-09-04 06:00:00"))
    assert counter.value(112.0) == pytest.approx(2.0)


def test_period_counter_closes_missed_cycles_after_downtime() -> None:
    """Home Assistant mati melewati beberapa hari: siklus lama langsung ditutup."""
    counter = PeriodCounter(CyclePeriod.DAY, CycleConfig())
    counter.sync(100.0, _at("2026-09-01 08:00:00"))

    # Hidup lagi tiga hari kemudian; pemakaian selama mati tidak ditumpuk ke
    # penghitung "hari ini".
    counter.sync(150.0, _at("2026-09-04 09:00:00"))
    assert counter.value(150.0) == pytest.approx(0.0)
    assert counter.cycle_start_at == _at("2026-09-04 00:00:00")


def test_period_counter_never_goes_negative() -> None:
    """Penghitung periode tidak pernah negatif, apa pun urutan pemanggilannya."""
    counter = PeriodCounter(
        CyclePeriod.DAY,
        CycleConfig(),
        PeriodCounterState(cycle_start=_at("2026-09-03 00:00:00").isoformat(), start_total=100.0),
    )
    assert counter.value(90.0) == pytest.approx(0.0)


def test_period_counter_has_no_value_before_data() -> None:
    """Belum ada data berarti belum ada angka - bukan nol palsu."""
    counter = PeriodCounter(CyclePeriod.DAY, CycleConfig())
    counter.sync(None, _at("2026-09-03 08:00:00"))
    assert counter.value(None) is None


def test_period_counter_survives_restart() -> None:
    """Titik awal siklus tidak hilang saat restart di tengah hari."""
    counter = PeriodCounter(CyclePeriod.DAY, CycleConfig())
    counter.sync(100.0, _at("2026-09-03 08:00:00"))
    counter.sync(103.0, _at("2026-09-03 12:00:00"))

    restored = PeriodCounter(
        CyclePeriod.DAY,
        CycleConfig(),
        PeriodCounterState.from_dict(counter.state.as_dict()),
    )
    restored.sync(105.0, _at("2026-09-03 14:00:00"))
    assert restored.value(105.0) == pytest.approx(5.0)


def test_all_periods_track_independently() -> None:
    """Jam, hari, minggu, bulan, tahun berjalan sendiri-sendiri."""
    config = CycleConfig()
    start = _at("2026-09-03 10:30:00")
    counters = {
        period: PeriodCounter(period, config) for period in CyclePeriod
    }
    for counter in counters.values():
        counter.sync(1000.0, start)

    later = start + timedelta(minutes=45)
    for counter in counters.values():
        counter.sync(1002.0, later)

    # Penghitung jam sudah berganti siklus (10:30 -> 11:15), sisanya belum.
    assert counters[CyclePeriod.HOUR].value(1002.0) == pytest.approx(0.0)
    for period in (
        CyclePeriod.DAY,
        CyclePeriod.WEEK,
        CyclePeriod.MONTH,
        CyclePeriod.YEAR,
    ):
        assert counters[period].value(1002.0) == pytest.approx(2.0)
