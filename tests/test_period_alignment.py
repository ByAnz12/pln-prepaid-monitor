"""kWh dan Rupiah pada satu baris harus berasal dari periode yang sama.

Kartu rincian menyandingkan dua statistik yang berbeda - energi dan biaya -
dalam satu tabel. Keduanya dibaca terpisah, dan panjangnya tidak selalu sama:
biaya baru mulai tercatat sejak tarif dipasang, dan sehari bisa hilang kalau
Home Assistant sempat mati.

Dulu barisnya dicocokkan **berdasarkan posisi ke-N dari ujung daftar**. Satu
hari yang hilang di daftar biaya menggeser seluruh baris sesudahnya, jadi kWh
dan Rupiah pada satu baris datang dari hari yang berbeda - dan hasilnya tetap
terlihat masuk akal. Pemilik melaporkannya sebagai "Rupiah tidak konsisten":
baris yang menunjukkan 521 dan 754 Rp/kWh padahal tarifnya 1212.

Test lama tidak menangkapnya karena semuanya ditulis dalam UTC, dan seluruh
daftarnya selalu sama panjang. Dua kondisi itu tidak pernah terjadi bersamaan
di instalasi sungguhan.

Ukuran yang dipakai di sini sederhana dan tidak bisa ditawar: **Rupiah dibagi
kWh harus sama dengan tarif**, di setiap baris dan di rata-rata. Kalau tidak,
ada dua periode berbeda yang sedang disandingkan.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.pln_prepaid_monitor.engines.period_summary import (
    past_day_keys,
    past_month_keys,
    summarise,
)

# Zona yang jauh dari UTC, sengaja: di UTC bug pergeseran tanggalnya tidak
# pernah muncul, dan itulah sebabnya ia lolos bertahun-tahun.
JAKARTA = ZoneInfo("Asia/Jakarta")
TARIF = 1212.0
NOW = datetime(2026, 9, 6, 9, 17, tzinfo=JAKARTA)


def _day(offset: int) -> datetime:
    """Awal hari lokal, ``offset`` hari sebelum hari ini."""
    return datetime(2026, 9, 6, tzinfo=JAKARTA) - timedelta(days=offset)


def _month(year: int, month: int) -> datetime:
    """Awal bulan lokal."""
    return datetime(year, month, 1, tzinfo=JAKARTA)


def _paired(energy, cost, now=NOW, monthly=False):
    """Dua ringkasan, dihitung persis seperti coordinator menghitungnya."""
    key = past_month_keys if monthly else past_day_keys
    shared = key(energy, now) & key(cost, now)
    kwargs = {"only_months" if monthly else "only_days": shared}
    empty: list = []
    if monthly:
        return (
            summarise(daily=empty, monthly=energy, now=now, **kwargs),
            summarise(daily=empty, monthly=cost, now=now, **kwargs),
        )
    return (
        summarise(daily=energy, monthly=empty, now=now, **kwargs),
        summarise(daily=cost, monthly=empty, now=now, **kwargs),
    )


def _rate(kwh, rp):
    """Rp per kWh, atau None kalau salah satu sisinya kosong."""
    return None if not kwh or rp is None else round(rp / kwh, 6)


# --- pasangan per hari -------------------------------------------------------


def test_a_gap_in_the_cost_data_does_not_shift_the_other_rows() -> None:
    """Inti keluhan pemilik.

    Daftar biaya kehilangan satu hari di tengah. Setiap rupiah yang ada di
    dalamnya tetap tarif dikali kWh - tidak ada satu pun salah hitung - jadi
    kalau ada baris yang rasionya bukan tarif, itu murni salah pasangan.
    """
    energy = [(_day(4), 22.0), (_day(3), 8.57), (_day(2), 38.23), (_day(1), 13.97)]
    cost = [(_day(4), 22.0 * TARIF), (_day(2), 38.23 * TARIF), (_day(1), 13.97 * TARIF)]

    e, c = _paired(energy, cost)

    for offset in (1, 2, 3):
        key = f"prev_day_{offset}"
        rate = _rate(e[key], c[key])
        assert rate in (None, TARIF), f"{key}: {rate} Rp/kWh, seharusnya {TARIF}"


def test_a_day_missing_on_one_side_shows_empty_not_another_days_number() -> None:
    """Kosong itu jujur. Angka hari lain yang dipasang di situ tidak."""
    energy = [(_day(3), 8.57), (_day(2), 38.23), (_day(1), 13.97)]
    cost = [(_day(2), 38.23 * TARIF), (_day(1), 13.97 * TARIF)]

    e, c = _paired(energy, cost)

    assert e["prev_day_3"] == 8.57
    assert c["prev_day_3"] is None


def test_the_average_is_divided_by_the_same_number_of_days_on_both_sides() -> None:
    """Rata-rata Rupiah dibagi jumlah hari yang berbeda tidak akan pernah cocok."""
    energy = [(_day(n), 10.0 * n) for n in (4, 3, 2, 1)]
    # Biaya baru tercatat sejak tarif dipasang - dua hari lebih pendek.
    cost = [(_day(n), 10.0 * n * TARIF) for n in (2, 1)]

    e, c = _paired(energy, cost)

    assert _rate(e["avg_daily"], c["avg_daily"]) == TARIF
    # Turunannya ikut rukun, karena avg_hourly memang diturunkan dari avg_daily.
    assert _rate(e["avg_hourly"], c["avg_hourly"]) == TARIF


def test_yesterday_is_yesterday_even_far_from_utc() -> None:
    """Bucket harian dimulai tengah malam **lokal**.

    Di Jakarta bucket "hari ini" bertanggal UTC kemarin. Dulu perbandingannya
    memakai tanggal UTC melawan ``now`` lokal, jadi hari berjalan lolos dan
    setiap baris bergeser sehari: "kemarin" sebenarnya menampilkan hari ini.
    """
    daily = [(_day(2), 38.23), (_day(1), 13.97), (_day(0), 3.10)]

    summary = summarise(daily=daily, monthly=[], now=NOW)

    assert summary["prev_day_1"] == 13.97, "kemarin tergeser jadi hari ini"
    assert summary["prev_day_2"] == 38.23
    assert 3.10 not in summary.values(), "hari berjalan ikut terhitung"


def test_the_running_day_never_enters_the_average() -> None:
    """Pukul sembilan pagi, hari ini baru terisi seperempat."""
    full = [(_day(3), 20.0), (_day(2), 20.0), (_day(1), 20.0)]

    without = summarise(daily=full, monthly=[], now=NOW)
    with_today = summarise(daily=[*full, (_day(0), 3.0)], monthly=[], now=NOW)

    assert without["avg_daily"] == 20.0
    assert with_today["avg_daily"] == 20.0


# --- pasangan per bulan ------------------------------------------------------


def test_month_rows_pair_by_calendar_month_across_the_year_boundary() -> None:
    """Januari harus menemukan Desember tahun lalu, bukan baris ke-N dari ujung."""
    january = datetime(2026, 1, 20, 10, 0, tzinfo=JAKARTA)
    energy = [
        (_month(2025, 10), 50.0),
        (_month(2025, 11), 60.0),
        (_month(2025, 12), 70.0),
    ]
    cost = [(_month(2025, 10), 50.0 * TARIF), (_month(2025, 12), 70.0 * TARIF)]

    e, c = _paired(energy, cost, now=january, monthly=True)

    assert e["prev_month_1"] == 70.0
    assert e["prev_month_2"] == 60.0
    assert e["prev_month_3"] == 50.0
    assert c["prev_month_2"] is None, "November tidak ada di sisi biaya"
    for offset in (1, 2, 3):
        key = f"prev_month_{offset}"
        assert _rate(e[key], c[key]) in (None, TARIF)


def test_the_running_month_is_never_counted_as_last_month() -> None:
    """Bulan berjalan sama saja dengan hari berjalan."""
    monthly = [(_month(2026, 7), 60.0), (_month(2026, 8), 70.0), (_month(2026, 9), 5.0)]

    summary = summarise(daily=[], monthly=monthly, now=NOW)

    assert summary["prev_month_1"] == 70.0
    assert summary["avg_monthly"] == 65.0


# --- tanpa tarif -------------------------------------------------------------


@pytest.mark.parametrize("only", [None, set()])
def test_energy_alone_still_summarises(only) -> None:
    """Kelompok tanpa tarif hanya punya satu statistik, dan itu sah.

    ``only_days`` kosong artinya tidak ada irisan sama sekali - rata-ratanya
    memang tidak bisa dihitung, tapi baris per hari tetap harus terisi.
    """
    daily = [(_day(2), 10.0), (_day(1), 20.0)]

    summary = summarise(daily=daily, monthly=[], now=NOW, only_days=only)

    assert summary["prev_day_1"] == 20.0
    assert summary["prev_day_2"] == 10.0
    assert summary["avg_daily"] == (15.0 if only is None else None)


# --- konversi waktu di sisi Home Assistant -----------------------------------


async def test_bucket_starts_come_back_in_local_time(
    recorder_mock, hass
) -> None:
    """Recorder menyimpan awal bucket sebagai UTC; engine menuntut waktu lokal.

    Ini baris yang benar-benar rusak. Bucket harian dimulai tengah malam lokal,
    dan di Jakarta itu tersimpan sebagai pukul 17:00 UTC **hari sebelumnya**.
    Tanpa konversi, ``start.date()`` menunjuk tanggal yang salah, saringan
    periode berjalan meloloskan hari ini, dan setiap baris bergeser sehari.

    Dijaga di sini, bukan di engine, karena engine sengaja tetap murni.
    """
    from unittest.mock import patch

    from homeassistant.util import dt as dt_util

    from custom_components.pln_prepaid_monitor.statistics_helper import (
        async_fetch_period_changes,
    )

    await hass.config.async_set_time_zone("Asia/Jakarta")
    local_midnight = datetime(2026, 9, 6, tzinfo=JAKARTA)

    def _fake(hass_, start, end, ids, period, units, types):
        return {"sensor.x": [{"start": local_midnight.timestamp(), "change": 12.5}]}

    with patch(
        "homeassistant.components.recorder.statistics.statistics_during_period",
        _fake,
    ):
        rows = await async_fetch_period_changes(
            hass, "sensor.x", "day", local_midnight - timedelta(days=7)
        )

    assert len(rows) == 1
    start, value = rows[0]
    assert value == 12.5
    assert start == local_midnight
    assert start.date() == local_midnight.date(), (
        "tanggalnya bergeser - engine akan menyandingkan hari yang salah"
    )
    assert start.utcoffset() == timedelta(hours=7), "bukan waktu lokal"
    # Pembanding di engine adalah ``now`` lokal, jadi keduanya harus sezona.
    assert start.date() == dt_util.as_local(start).date()
