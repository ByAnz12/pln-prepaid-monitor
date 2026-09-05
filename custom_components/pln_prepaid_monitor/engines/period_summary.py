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

from datetime import datetime
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


def summarise(
    *,
    daily: list[tuple[datetime, float]],
    monthly: list[tuple[datetime, float]],
    now: datetime,
) -> dict[str, float | None]:
    """Susun angka per baris dari data statistik.

    Semua masukan berupa daftar ``(awal periode, konsumsi)`` urut dari yang
    paling lama, persis seperti yang dikembalikan recorder.

    Periode **yang sedang berjalan tidak ikut** dihitung ke rata-rata maupun ke
    baris "kemarin": jam yang baru berjalan lima menit akan menyeret rata-rata
    turun tanpa alasan, dan itu jenis kesalahan yang tidak kelihatan salah.
    """
    summary: dict[str, float | None] = {key: None for key in ROW_KEYS}

    past_days = [(start, value) for start, value in daily if start.date() < now.date()]
    summary["avg_daily"] = _mean([value for _, value in past_days])

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
        if len(past_days) >= offset:
            summary[f"prev_day_{offset}"] = past_days[-offset][1]

    past_months = [
        (start, value)
        for start, value in monthly
        if (start.year, start.month) < (now.year, now.month)
    ]
    summary["avg_monthly"] = _mean([value for _, value in past_months])
    for offset in (1, 2, 3):
        if len(past_months) >= offset:
            summary[f"prev_month_{offset}"] = past_months[-offset][1]

    return summary


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
