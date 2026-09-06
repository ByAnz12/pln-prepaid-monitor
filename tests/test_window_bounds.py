"""Rentang sampel prediksi hanya boleh berisi periode yang sudah selesai.

Kesalahan yang dijaga di sini tidak pernah tampil sebagai error. Sebelumnya
rentangnya dihitung ``now - span`` tanpa batas akhir, jadi hari yang sedang
berjalan ikut terhitung sebagai kalau-kalau hari penuh. Pukul sembilan pagi
"hari ini" baru terisi seperempatnya, dan potongan itu menyeret median turun -
perkiraan token habis jadi terlihat jauh lebih panjang daripada kenyataannya.

Persis itu yang dilaporkan pemilik: kartu Token menyebut rata-rata harian
13,97 kWh sementara tabel Pemakaian & biaya menyebut 20,26 kWh untuk hari yang
sama, dan perkiraan melompat dari 15 hari ke 43 hari. Tabel itu benar -
``engines/period_summary.py`` memang sudah membuang periode berjalan sejak
awal. Yang ketinggalan adalah jalur prediksi.

Arah kesalahannya yang membuatnya layak dikunci: perkiraan jadi terlalu
**optimistis**. Token habis lebih cepat daripada yang dijanjikan layar, dan
tidak ada satu pun tanda bahwa angkanya keliru.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from homeassistant.util import dt as dt_util

from custom_components.pln_prepaid_monitor.engines.prediction_engine import (
    WINDOW_24H,
    WINDOW_7D,
    WINDOW_30D,
    WINDOW_SPECS,
)
from custom_components.pln_prepaid_monitor.statistics_helper import window_bounds

JAKARTA = ZoneInfo("Asia/Jakarta")


@pytest.fixture(autouse=True)
def _jakarta_timezone():
    """Zona waktu tetap, supaya "awal hari" punya arti yang pasti."""
    original = dt_util.get_default_time_zone()
    dt_util.set_default_time_zone(JAKARTA)
    yield
    dt_util.set_default_time_zone(original)


# Pagi hari, sengaja: saat itulah hari berjalan paling kosong dan paling
# merusak rata-rata kalau ikut terhitung.
MORNING = datetime(2026, 9, 6, 9, 17, 42, tzinfo=JAKARTA)


@pytest.mark.parametrize("window", [WINDOW_7D, WINDOW_30D])
def test_daily_windows_stop_at_midnight_today(window: str) -> None:
    """Hari yang sedang berjalan tidak boleh ikut jadi sampel.

    Batas akhirnya eksklusif di sisi recorder, jadi bucket yang mulai tepat
    tengah malam hari ini - yaitu hari berjalan - memang tidak terbawa.
    """
    start, end = window_bounds(WINDOW_SPECS[window], MORNING)

    assert end == datetime(2026, 9, 6, 0, 0, tzinfo=JAKARTA)
    assert end <= MORNING
    # Awalnya pun tengah malam, bukan pukul 09:17 sekian hari lalu.
    assert start.hour == 0 and start.minute == 0


@pytest.mark.parametrize("window", [WINDOW_7D, WINDOW_30D])
def test_daily_windows_hold_exactly_the_promised_number_of_days(window: str) -> None:
    """Tujuh hari harus benar-benar tujuh hari penuh, bukan enam setengah."""
    spec = WINDOW_SPECS[window]
    start, end = window_bounds(spec, MORNING)

    assert end - start == spec.span


def test_hourly_window_stops_at_the_top_of_this_hour() -> None:
    """Jam berjalan sama saja: pukul 09:17 berarti jam 09 baru seperempat."""
    start, end = window_bounds(WINDOW_SPECS[WINDOW_24H], MORNING)

    assert end == datetime(2026, 9, 6, 9, 0, tzinfo=JAKARTA)
    assert end - start == timedelta(hours=24)


def test_a_utc_now_still_lands_on_the_right_local_day() -> None:
    """Jebakan zona waktu: ``start_of_local_day`` membaca ``.date()`` apa adanya.

    Pukul 00:30 di Jakarta masih tanggal 5 di UTC. Kalau ``now`` yang datang
    berupa UTC dan tidak diselaraskan dulu, batasnya meleset sehari penuh -
    dan seluruh sampel bergeser tanpa ada yang kelihatan salah.
    """
    just_after_midnight = datetime(2026, 9, 6, 0, 30, tzinfo=JAKARTA)
    as_utc = just_after_midnight.astimezone(dt_util.UTC)
    assert as_utc.date() != just_after_midnight.date(), "premis test tidak berlaku"

    from_local = window_bounds(WINDOW_SPECS[WINDOW_7D], just_after_midnight)
    from_utc = window_bounds(WINDOW_SPECS[WINDOW_7D], as_utc)

    assert from_local == from_utc


def test_the_running_period_is_never_inside_the_range() -> None:
    """Diperiksa untuk sepanjang hari, bukan cuma satu jam yang kebetulan enak."""
    for hour in range(24):
        now = MORNING.replace(hour=hour, minute=59, second=59)
        for spec in WINDOW_SPECS.values():
            start, end = window_bounds(spec, now)
            assert start < end, spec.key
            assert end <= now, f"{spec.key} pukul {hour}: batas akhir di masa depan"
