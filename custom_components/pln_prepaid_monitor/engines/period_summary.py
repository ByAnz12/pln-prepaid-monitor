"""Ringkasan pemakaian dan biaya per periode, untuk kartu rincian di dashboard.

*Pure Python*, jadi seluruh aritmetikanya bisa diuji tanpa Home Assistant.

Kenapa disusun jadi satu ringkasan, bukan belasan entity baru: baris seperti
"3 bulan lalu" adalah angka untuk **dibaca**, bukan untuk dipakai automation.
Membuat entity terpisah untuk masing-masing berarti belasan entity baru per
kelompok tagihan, masing-masing dengan riwayat sendiri di database - biaya yang
tidak sepadan dengan manfaatnya. Sebagai satu atribut, angkanya tetap terlihat
di dashboard dan tetap bisa dibaca template kalau memang dibutuhkan.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

# Semua baris yang bisa dipilih user. Urutan di sini adalah urutan tampilnya.
ROW_KEYS: tuple[str, ...] = (
    "avg_hourly",
    "avg_daily",
    "avg_monthly",
    "this_hour",
    "this_day",
    "prev_day_1",
    "prev_day_2",
    "prev_day_3",
    "this_week",
    "this_month",
    "prev_month_1",
    "prev_month_2",
    "prev_month_3",
    "this_year",
)

# Bawaan mengikuti permintaan user: tiga rata-rata, empat hari terakhir, dan
# empat bulan terakhir.
DEFAULT_ROWS: tuple[str, ...] = (
    "avg_hourly",
    "avg_daily",
    "avg_monthly",
    "this_day",
    "prev_day_1",
    "prev_day_2",
    "prev_day_3",
    "this_month",
    "prev_month_1",
    "prev_month_2",
    "prev_month_3",
)

# Baris yang diambil dari penghitung siklus yang berjalan langsung, bukan dari
# statistik: angkanya hidup, sementara statistik baru disusun tiap jam.
LIVE_ROWS: tuple[str, ...] = (
    "this_hour",
    "this_day",
    "this_week",
    "this_month",
    "this_year",
)

# Berapa banyak periode lampau yang perlu dibaca. Cukup untuk mengisi tiga
# bulan lalu plus satu bulan berjalan, dan tiga hari lalu plus hari ini.
DAYS_TO_FETCH = 40
MONTHS_TO_FETCH = 5


def _mean(values: list[float]) -> float | None:
    """Rata-rata, atau None kalau tidak ada datanya sama sekali."""
    if not values:
        return None
    return sum(values) / len(values)


def past_day_keys(
    daily: list[tuple[datetime, float]], now: datetime
) -> set[date]:
    """Tanggal hari lampau yang datanya ada. Waktu masuk harus sudah lokal."""
    return {start.date() for start, _ in daily if start.date() < now.date()}


def past_month_keys(
    monthly: list[tuple[datetime, float]], now: datetime
) -> set[tuple[int, int]]:
    """Bulan lampau yang datanya ada, sebagai pasangan (tahun, bulan)."""
    return {
        (start.year, start.month)
        for start, _ in monthly
        if (start.year, start.month) < (now.year, now.month)
    }


def summarise(
    *,
    daily: list[tuple[datetime, float]],
    monthly: list[tuple[datetime, float]],
    now: datetime,
    only_days: set[date] | None = None,
    only_months: set[tuple[int, int]] | None = None,
) -> dict[str, float | None]:
    """Susun angka per baris dari data statistik.

    Semua masukan berupa daftar ``(awal periode, konsumsi)``. Awal periodenya
    harus sudah **waktu lokal** - recorder menyimpannya sebagai UTC, dan
    ``async_fetch_period_changes`` yang menyelaraskannya.

    Periode **yang sedang berjalan tidak ikut** dihitung ke rata-rata maupun ke
    baris "kemarin": jam yang baru berjalan lima menit akan menyeret rata-rata
    turun tanpa alasan, dan itu jenis kesalahan yang tidak kelihatan salah.

    Baris "N hari lalu" dicari **berdasarkan tanggalnya**, bukan berdasarkan
    posisi ke-N dari ujung daftar. Bedanya menentukan: kartu rincian
    menyandingkan kWh dan Rupiah dari dua statistik yang berbeda, dan kedua
    daftar itu tidak selalu sama panjang - biaya baru tercatat sejak tarif
    dipasang, dan sehari bisa hilang kalau Home Assistant sempat mati. Dengan
    penomoran posisi, satu hari yang hilang di daftar biaya membuat seluruh
    baris sesudahnya menyandingkan kWh dan Rupiah dari **hari yang berbeda** -
    dan hasilnya tetap terlihat masuk akal.

    ``only_days`` dan ``only_months`` membatasi rata-rata ke periode yang ada
    di **kedua** statistik. Tanpa itu, rata-rata Rupiah dibagi jumlah hari yang
    berbeda dari rata-rata kWh, jadi Rp/kWh-nya tidak pernah cocok dengan tarif
    mana pun.
    """
    summary: dict[str, float | None] = {key: None for key in ROW_KEYS}

    by_day: dict[date, float] = {
        start.date(): value for start, value in daily if start.date() < now.date()
    }
    shared_days = by_day if only_days is None else {
        day: value for day, value in by_day.items() if day in only_days
    }
    summary["avg_daily"] = _mean(list(shared_days.values()))

    # Rata-rata per jam diturunkan dari rata-rata harian, bukan dihitung
    # terpisah dari statistik per jam.
    #
    # Alasannya kejelasan, bukan kemalasan: kedua angka punya jendela data yang
    # berbeda, jadi kalau dihitung sendiri-sendiri "rata-rata per jam" dikali 24
    # tidak sama dengan "rata-rata harian" - dan pembaca kartu tidak punya cara
    # tahu kenapa. Diturunkan begini, keduanya selalu rukun.
    if summary["avg_daily"] is not None:
        summary["avg_hourly"] = summary["avg_daily"] / 24
    for offset in (1, 2, 3):
        summary[f"prev_day_{offset}"] = by_day.get(now.date() - timedelta(days=offset))

    by_month: dict[tuple[int, int], float] = {
        (start.year, start.month): value
        for start, value in monthly
        if (start.year, start.month) < (now.year, now.month)
    }
    shared_months = by_month if only_months is None else {
        month: value for month, value in by_month.items() if month in only_months
    }
    summary["avg_monthly"] = _mean(list(shared_months.values()))
    for offset in (1, 2, 3):
        summary[f"prev_month_{offset}"] = by_month.get(_month_before(now, offset))

    return summary


def _month_before(now: datetime, offset: int) -> tuple[int, int]:
    """Bulan ke-``offset`` sebelum bulan berjalan, sebagai (tahun, bulan)."""
    index = now.year * 12 + (now.month - 1) - offset
    return divmod(index, 12)[0], divmod(index, 12)[1] + 1


def selected_rows(configured: Any) -> list[str]:
    """Baris yang dipilih user, dibersihkan dan diurutkan ulang.

    Urutannya selalu mengikuti ``ROW_KEYS``, bukan urutan user mencentangnya -
    daftar yang urutannya berubah-ubah antar kelompok tagihan lebih sulit
    dibaca daripada daftar yang selalu sama.
    """
    if not configured:
        return list(DEFAULT_ROWS)
    chosen = {str(row) for row in configured}
    return [key for key in ROW_KEYS if key in chosen]
