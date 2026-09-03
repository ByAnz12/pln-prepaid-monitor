"""Test batas siklus - murni aritmetika tanggal, tanpa Home Assistant."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from custom_components.pln_prepaid_monitor.engines.period import (
    CycleConfig,
    CyclePeriod,
    cycle_start,
    next_cycle_start,
)

JAKARTA = ZoneInfo("Asia/Jakarta")


def _at(text: str) -> datetime:
    """Waktu lokal Jakarta dari string, supaya test enak dibaca."""
    return datetime.fromisoformat(text).replace(tzinfo=JAKARTA)


DEFAULT = CycleConfig()


def test_hour_boundary_follows_the_clock() -> None:
    """Jam mengikuti jarum jam biasa, tidak digeser jam mulai hari."""
    config = CycleConfig(day_start_time=time(6, 30))
    assert cycle_start(CyclePeriod.HOUR, _at("2026-09-03 14:37:21"), config) == _at(
        "2026-09-03 14:00:00"
    )
    assert next_cycle_start(
        CyclePeriod.HOUR, _at("2026-09-03 14:37:21"), config
    ) == _at("2026-09-03 15:00:00")


def test_day_boundary_default_midnight() -> None:
    """Hari standar dimulai tengah malam."""
    assert cycle_start(CyclePeriod.DAY, _at("2026-09-03 14:37:00"), DEFAULT) == _at(
        "2026-09-03 00:00:00"
    )
    assert cycle_start(CyclePeriod.DAY, _at("2026-09-03 00:00:00"), DEFAULT) == _at(
        "2026-09-03 00:00:00"
    )


def test_day_boundary_custom_start_time() -> None:
    """Jam mulai hari bisa diatur, misalnya mengikuti jam buka toko."""
    config = CycleConfig(day_start_time=time(6, 0))

    # Jam 5 pagi masih terhitung "hari kemarin".
    assert cycle_start(CyclePeriod.DAY, _at("2026-09-03 05:00:00"), config) == _at(
        "2026-09-02 06:00:00"
    )
    # Jam 7 pagi sudah masuk hari baru.
    assert cycle_start(CyclePeriod.DAY, _at("2026-09-03 07:00:00"), config) == _at(
        "2026-09-03 06:00:00"
    )


def test_week_boundary_default_monday() -> None:
    """Minggu standar dimulai Senin tengah malam."""
    # 3 September 2026 adalah hari Kamis.
    assert cycle_start(CyclePeriod.WEEK, _at("2026-09-03 14:00:00"), DEFAULT) == _at(
        "2026-08-31 00:00:00"
    )
    assert next_cycle_start(
        CyclePeriod.WEEK, _at("2026-09-03 14:00:00"), DEFAULT
    ) == _at("2026-09-07 00:00:00")


def test_week_boundary_custom_start_day() -> None:
    """Hari mulai minggu bisa diatur."""
    config = CycleConfig(week_start_day="sunday")
    assert cycle_start(CyclePeriod.WEEK, _at("2026-09-03 14:00:00"), config) == _at(
        "2026-08-30 00:00:00"
    )


def test_week_boundary_respects_day_start_time() -> None:
    """Batas minggu ikut bergeser kalau jam mulai hari digeser."""
    config = CycleConfig(day_start_time=time(6, 0), week_start_day="monday")
    # Senin jam 3 pagi masih terhitung minggu sebelumnya.
    assert cycle_start(CyclePeriod.WEEK, _at("2026-08-31 03:00:00"), config) == _at(
        "2026-08-24 06:00:00"
    )


def test_month_boundary_default_first() -> None:
    """Bulan standar dimulai tanggal 1."""
    assert cycle_start(CyclePeriod.MONTH, _at("2026-09-03 14:00:00"), DEFAULT) == _at(
        "2026-09-01 00:00:00"
    )
    assert next_cycle_start(
        CyclePeriod.MONTH, _at("2026-09-03 14:00:00"), DEFAULT
    ) == _at("2026-10-01 00:00:00")


def test_month_boundary_custom_day() -> None:
    """Tanggal mulai bulan bisa diatur, termasuk melewati pergantian tahun."""
    config = CycleConfig(month_start_day=15)
    assert cycle_start(CyclePeriod.MONTH, _at("2026-09-03 14:00:00"), config) == _at(
        "2026-08-15 00:00:00"
    )
    assert cycle_start(CyclePeriod.MONTH, _at("2026-01-05 14:00:00"), config) == _at(
        "2025-12-15 00:00:00"
    )
    assert next_cycle_start(
        CyclePeriod.MONTH, _at("2026-12-20 14:00:00"), config
    ) == _at("2027-01-15 00:00:00")


def test_month_start_day_is_capped_at_28() -> None:
    """Tanggal mulai bulan dibatasi 28 supaya selalu ada di Februari."""
    config = CycleConfig.from_dict({"month_start_day": 31})
    assert config.month_start_day == 28
    assert cycle_start(CyclePeriod.MONTH, _at("2026-03-01 10:00:00"), config) == _at(
        "2026-02-28 00:00:00"
    )


def test_year_boundary_default_january() -> None:
    """Tahun standar dimulai 1 Januari."""
    assert cycle_start(CyclePeriod.YEAR, _at("2026-09-03 14:00:00"), DEFAULT) == _at(
        "2026-01-01 00:00:00"
    )
    assert next_cycle_start(
        CyclePeriod.YEAR, _at("2026-09-03 14:00:00"), DEFAULT
    ) == _at("2027-01-01 00:00:00")


def test_year_boundary_custom_month() -> None:
    """Bulan mulai tahun bisa diatur."""
    config = CycleConfig(year_start_month="july")
    assert cycle_start(CyclePeriod.YEAR, _at("2026-09-03 14:00:00"), config) == _at(
        "2026-07-01 00:00:00"
    )
    # Sebelum Juli masih terhitung tahun siklus sebelumnya.
    assert cycle_start(CyclePeriod.YEAR, _at("2026-03-03 14:00:00"), config) == _at(
        "2025-07-01 00:00:00"
    )


def test_cycle_start_is_never_in_the_future() -> None:
    """Aturan dasar: awal siklus selalu sudah lewat."""
    now = _at("2026-09-03 14:37:21")
    config = CycleConfig(
        day_start_time=time(6, 30),
        week_start_day="wednesday",
        month_start_day=15,
        year_start_month="june",
    )
    for period in CyclePeriod:
        assert cycle_start(period, now, config) <= now
        assert next_cycle_start(period, now, config) > now


def test_config_from_dict_tolerates_bad_input() -> None:
    """Data konfigurasi rusak tidak boleh membuat integrasi gagal dimuat."""
    config = CycleConfig.from_dict(
        {
            "day_start_time": "bukan jam",
            "week_start_day": "hari-libur",
            "month_start_day": 0,
            "year_start_month": "bulan-aneh",
        }
    )
    assert config.day_start_time == time(0, 0)
    assert config.week_start_index == 0
    assert config.month_start_day == 1
    assert config.year_start_month_number == 1


def test_config_from_dict_reads_time_string() -> None:
    """Jam mulai dari TimeSelector berbentuk teks 'HH:MM:SS'."""
    config = CycleConfig.from_dict({"day_start_time": "06:30:00"})
    assert config.day_start_time == time(6, 30)


def test_unknown_period_is_rejected_loudly() -> None:
    """Periode tak dikenal harus gagal jelas, bukan diam-diam salah hitung."""
    with pytest.raises(ValueError):
        cycle_start("decade", _at("2026-09-03 14:00:00"), DEFAULT)
