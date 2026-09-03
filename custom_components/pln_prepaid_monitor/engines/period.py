"""Perhitungan batas siklus untuk penghitung per periode.

Modul ini *pure Python* (hanya butuh ``datetime``), jadi seluruh aturan batas
siklus bisa diuji sebagai fungsi biasa tanpa menjalankan Home Assistant.

Semua batas dihitung dalam **waktu lokal**, karena "hari ini" bagi user berarti
hari menurut jam dinding di rumahnya, bukan menurut UTC.

Tidak ada satu pun batas siklus yang dikunci di dalam kode: jam mulai hari,
hari mulai minggu, tanggal mulai bulan, dan bulan mulai tahun semuanya diambil
dari konfigurasi user (spec Bagian E dan aturan "semua periode wajib
configurable").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Any


class CyclePeriod(StrEnum):
    """Periode penghitung yang tersedia."""

    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


ALL_PERIODS: tuple[str, ...] = tuple(period.value for period in CyclePeriod)

WEEKDAYS: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

MONTHS: tuple[str, ...] = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)

# Dibatasi 28 supaya tanggal mulai bulan selalu ada di setiap bulan, termasuk
# Februari di tahun non-kabisat. Kalau suatu saat perlu tanggal 29-31, aturannya
# harus diputuskan dulu (mundur ke hari terakhir bulan itu?) - jangan ditebak.
MAX_MONTH_START_DAY = 28


@dataclass(frozen=True)
class CycleConfig:
    """Di mana batas tiap siklus jatuh, menurut pengaturan user."""

    day_start_time: time = time(0, 0)
    week_start_day: str = "monday"
    month_start_day: int = 1
    year_start_month: str = "january"

    @property
    def week_start_index(self) -> int:
        """Indeks hari mulai minggu, 0 = Senin (sama seperti ``weekday()``)."""
        try:
            return WEEKDAYS.index(self.week_start_day)
        except ValueError:
            return 0

    @property
    def year_start_month_number(self) -> int:
        """Nomor bulan mulai tahun, 1 = Januari."""
        try:
            return MONTHS.index(self.year_start_month) + 1
        except ValueError:
            return 1

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> CycleConfig:
        """Baca konfigurasi siklus dari data subentry."""
        data = data or {}
        raw_time = data.get("day_start_time")
        start_time = time(0, 0)
        if isinstance(raw_time, time):
            start_time = raw_time
        elif isinstance(raw_time, str) and raw_time:
            parts = raw_time.split(":")
            try:
                start_time = time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
            except (ValueError, IndexError):
                start_time = time(0, 0)

        month_start_day = int(data.get("month_start_day", 1) or 1)
        month_start_day = max(1, min(MAX_MONTH_START_DAY, month_start_day))

        return cls(
            day_start_time=start_time,
            week_start_day=str(data.get("week_start_day", "monday") or "monday"),
            month_start_day=month_start_day,
            year_start_month=str(data.get("year_start_month", "january") or "january"),
        )


def _at_start_time(day: date, config: CycleConfig, reference: datetime) -> datetime:
    """Gabungkan tanggal dengan jam mulai, tetap di zona waktu yang sama."""
    return datetime.combine(day, config.day_start_time, tzinfo=reference.tzinfo)


def cycle_start(period: str, now: datetime, config: CycleConfig) -> datetime:
    """Awal siklus yang sedang berjalan pada saat ``now`` (waktu lokal).

    Selalu mengembalikan waktu yang <= ``now``.
    """
    if period == CyclePeriod.HOUR:
        # Jam mengikuti jarum jam biasa (menit 0), bukan digeser oleh jam mulai
        # hari - supaya "pemakaian jam ini" berarti hal yang sama bagi semua orang.
        return now.replace(minute=0, second=0, microsecond=0)

    if period == CyclePeriod.DAY:
        candidate = _at_start_time(now.date(), config, now)
        if candidate > now:
            candidate = _at_start_time(now.date() - timedelta(days=1), config, now)
        return candidate

    if period == CyclePeriod.WEEK:
        day_start = _at_start_time(now.date(), config, now)
        if day_start > now:
            day_start = _at_start_time(now.date() - timedelta(days=1), config, now)
        offset = (day_start.weekday() - config.week_start_index) % 7
        return _at_start_time(day_start.date() - timedelta(days=offset), config, now)

    if period == CyclePeriod.MONTH:
        candidate = _at_start_time(
            now.date().replace(day=config.month_start_day), config, now
        )
        if candidate > now:
            candidate = _at_start_time(
                _previous_month(now.date(), config.month_start_day), config, now
            )
        return candidate

    if period == CyclePeriod.YEAR:
        candidate = _at_start_time(
            date(
                now.year, config.year_start_month_number, config.month_start_day
            ),
            config,
            now,
        )
        if candidate > now:
            candidate = _at_start_time(
                date(
                    now.year - 1,
                    config.year_start_month_number,
                    config.month_start_day,
                ),
                config,
                now,
            )
        return candidate

    raise ValueError(f"Periode tidak dikenal: {period}")


def next_cycle_start(period: str, now: datetime, config: CycleConfig) -> datetime:
    """Kapan siklus berikutnya dimulai (selalu > ``now``)."""
    current = cycle_start(period, now, config)

    if period == CyclePeriod.HOUR:
        return current + timedelta(hours=1)
    if period == CyclePeriod.DAY:
        return _at_start_time(current.date() + timedelta(days=1), config, now)
    if period == CyclePeriod.WEEK:
        return _at_start_time(current.date() + timedelta(days=7), config, now)
    if period == CyclePeriod.MONTH:
        return _at_start_time(
            _next_month(current.date(), config.month_start_day), config, now
        )
    if period == CyclePeriod.YEAR:
        return _at_start_time(
            date(
                current.year + 1,
                config.year_start_month_number,
                config.month_start_day,
            ),
            config,
            now,
        )

    raise ValueError(f"Periode tidak dikenal: {period}")


def _previous_month(day: date, month_start_day: int) -> date:
    """Tanggal mulai bulan sebelumnya."""
    year = day.year
    month = day.month - 1
    if month == 0:
        month = 12
        year -= 1
    return date(year, month, month_start_day)


def _next_month(day: date, month_start_day: int) -> date:
    """Tanggal mulai bulan berikutnya."""
    year = day.year
    month = day.month + 1
    if month == 13:
        month = 1
        year += 1
    return date(year, month, month_start_day)
