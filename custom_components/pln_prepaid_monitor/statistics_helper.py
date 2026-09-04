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

from .engines.prediction_engine import WINDOW_SPECS, PredictionConfig

_LOGGER = logging.getLogger(__name__)


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
        start = now - spec.span
        try:
            rows: dict[str, list[dict[str, Any]]] = (
                await instance.async_add_executor_job(
                    statistics_during_period,
                    hass,
                    start,
                    None,
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
