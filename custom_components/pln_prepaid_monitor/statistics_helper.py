"""Pembacaan long-term statistics milik Home Assistant.

Dipisahkan ke modul sendiri karena inilah satu-satunya tempat integrasi ini
menyentuh database recorder. Semua akses lewat API resmi
``statistics_during_period`` dan dijalankan di thread recorder lewat
``get_instance(hass).async_add_executor_job`` - bukan query SQL manual.
"""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .engines.prediction_engine import WINDOW_SPECS, PredictionConfig, WindowSpec

_LOGGER = logging.getLogger(__name__)


def window_bounds(spec: WindowSpec, now: datetime) -> tuple[datetime, datetime]:
    """Batas rentang yang **hanya** berisi periode yang sudah selesai.

    Ini bukan kerapian, melainkan koreksi angka. Sebelumnya rentangnya dihitung
    ``now - span`` tanpa batas akhir, jadi hari yang sedang berjalan ikut masuk
    sebagai kalau-kalau hari penuh. Pukul sembilan pagi, "hari ini" baru terisi
    seperempatnya - dan potongan itu menyeret rata-rata turun, yang membuat
    perkiraan token habis terlihat jauh lebih panjang daripada kenyataannya.

    Arah kesalahannya berbahaya: perkiraan jadi terlalu optimistis, dan user
    kehabisan token lebih cepat dari yang dijanjikan layar.

    ``engines/period_summary.py`` sudah membuang periode berjalan sejak awal.
    Prediksi ketinggalan, dan itulah sebabnya "Rata-rata harian" di kartu Token
    pernah menunjukkan angka berbeda dari tabel Pemakaian & biaya.

    Batas akhirnya eksklusif di sisi recorder (``start_ts < end_time_ts``,
    ``recorder/statistics.py``), jadi bucket yang mulai tepat di ``end`` -
    yaitu periode yang sedang berjalan - memang tidak ikut terbawa.
    """
    # Selalu diselaraskan ke waktu lokal dulu: ``start_of_local_day`` membaca
    # ``.date()`` apa adanya, jadi datetime UTC bisa jatuh ke tanggal yang salah
    # bagi instalasi yang zonanya jauh dari UTC.
    local = dt_util.as_local(now)
    if spec.period == "hour":
        end = local.replace(minute=0, second=0, microsecond=0)
    else:
        end = dt_util.start_of_local_day(local)
    return end - spec.span, end


def _recorder_available(hass: HomeAssistant) -> bool:
    """Recorder tidak wajib ada di setiap instalasi Home Assistant."""
    return "recorder" in hass.config.components


async def async_fetch_window_samples(
    hass: HomeAssistant,
    statistic_id: str,
    config: PredictionConfig,
    now: datetime,
) -> dict[str, list[float]]:
    """Ambil konsumsi per periode untuk tiap rentang yang mungkin dipakai.

    Memakai tipe statistik ``change``, yang sudah berisi selisih antar periode -
    jadi kita tidak perlu menghitung selisih ``sum`` sendiri dan tidak berisiko
    salah menangani pergantian siklus.
    """
    if not _recorder_available(hass):
        _LOGGER.debug("Recorder tidak aktif, prediksi dilewati")
        return {}

    from homeassistant.components.recorder import get_instance  # noqa: PLC0415
    from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
        statistics_during_period,
    )

    samples: dict[str, list[float]] = {}
    instance = get_instance(hass)

    for window, spec in WINDOW_SPECS.items():
        start, end = window_bounds(spec, now)
        try:
            rows: dict[str, list[dict[str, Any]]] = (
                await instance.async_add_executor_job(
                    statistics_during_period,
                    hass,
                    start,
                    end,
                    {statistic_id},
                    spec.period,
                    None,
                    {"change"},
                )
            )
        except Exception:  # noqa: BLE001
            # Kegagalan membaca statistik tidak boleh menjatuhkan integrasi;
            # prediksi cukup dilaporkan sebagai belum tersedia.
            _LOGGER.exception(
                "Gagal membaca statistik %s untuk rentang %s", statistic_id, window
            )
            continue

        values = [
            float(row["change"])
            for row in rows.get(statistic_id, [])
            if row.get("change") is not None and float(row["change"]) >= 0
        ]
        samples[window] = values

    return samples


async def async_fetch_period_changes(
    hass: HomeAssistant,
    statistic_id: str,
    period: str,
    start: datetime,
) -> list[tuple[datetime, float]]:
    """Konsumsi per periode sejak ``start``, urut dari yang paling lama.

    Dipakai untuk baris "hari kemarin", "bulan lalu", dan rata-rata. Sama
    seperti prediksi, memakai tipe ``change`` supaya tidak perlu menghitung
    selisih ``sum`` sendiri.
    """
    if not _recorder_available(hass):
        return []

    from homeassistant.components.recorder import get_instance  # noqa: PLC0415
    from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
        statistics_during_period,
    )

    try:
        rows: dict[str, list[dict[str, Any]]] = await get_instance(
            hass
        ).async_add_executor_job(
            statistics_during_period,
            hass,
            start,
            None,
            {statistic_id},
            period,
            None,
            {"change"},
        )
    except Exception:  # noqa: BLE001
        # Sama seperti prediksi: gagal membaca statistik tidak boleh
        # menjatuhkan integrasi, cukup dilaporkan sebagai belum tersedia.
        _LOGGER.exception(
            "Gagal membaca statistik %s untuk periode %s", statistic_id, period
        )
        return []

    out: list[tuple[datetime, float]] = []
    for row in rows.get(statistic_id, []):
        change = row.get("change")
        if change is None or float(change) < 0:
            continue
        # Diselaraskan ke waktu lokal di sini, bukan di engine: bucket harian
        # dimulai tengah malam **lokal**, tapi recorder menyimpannya sebagai UTC.
        #
        # Tanpa konversi ini, di Jakarta (UTC+7) bucket "hari ini" bertanggal
        # UTC kemarin. Saringan periode berjalan di period_summary jadi
        # meloloskannya, dan setiap baris "N hari lalu" bergeser sehari - "hari
        # kemarin" sebenarnya menampilkan hari ini. Tidak ada satu pun angka
        # yang terlihat mustahil, jadi tidak ada yang curiga.
        #
        # Engine-nya sengaja dibiarkan murni: ia cukup menerima waktu lokal.
        out.append(
            (dt_util.as_local(dt_util.utc_from_timestamp(row["start"])), float(change))
        )
    return out
