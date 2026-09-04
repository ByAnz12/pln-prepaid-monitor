"""Penghapusan statistik lama milik integrasi ini.

**Bagian paling rapuh di seluruh sistem, dan itu disengaja terbuka.**

Home Assistant tidak menyediakan API resmi untuk menghapus long-term statistics
lama secara selektif per rentang waktu (spec N.4, diverifikasi ke source
2026.8.3):

* ``recorder.purge_entities`` hanya membersihkan tabel ``states``/``events`` -
  tabel ``statistics`` sama sekali tidak tersentuh.
* ``recorder/clear_statistics`` bersifat semua-atau-tidak sama sekali, tanpa
  parameter rentang waktu, dan menghapus baris ``StatisticsMeta`` yang lewat
  ``ON DELETE CASCADE`` ikut menghapus **seluruh** riwayat entity itu.

Jadi satu-satunya jalan adalah menulis DELETE lewat model ORM recorder
langsung. Konsekuensinya jujur: bagian ini bergantung pada struktur internal
yang **bukan API publik**, jadi risikonya terhadap upgrade Home Assistant lebih
tinggi daripada bagian lain sistem ini.

Tiga pengaman yang dipasang karena itu:

1. :func:`check_supported` memeriksa model dan kolom yang dibutuhkan **sebelum**
   menyentuh apa pun. Kalau strukturnya berubah, integrasi gagal terang-terangan
   dengan pesan yang menyuruh user menghapus manual - bukan diam-diam salah
   hapus.
2. Baris ``StatisticsMeta`` **tidak pernah** ikut dihapus, supaya cascade tidak
   terpicu dan cache metadata milik recorder tidak korup.
3. Penghapusan dibatasi ke ``statistic_id`` milik entity buatan integrasi ini
   saja, dan dilakukan bertahap supaya tidak memegang write-lock lama.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

RETENTION_UNLIMITED = "unlimited"
RETENTION_OPTIONS = ("1", "2", "3", "5", RETENTION_UNLIMITED)

DAYS_PER_YEAR = 365.25

# Sekali hapus tidak boleh terlalu besar, supaya write-lock tidak dipegang lama
# dan proses kompaksi per jam milik recorder tidak terganggu.
MAX_ROWS_PER_BATCH = 1000


class RetentionUnsupportedError(RuntimeError):
    """Struktur internal recorder tidak seperti yang diharapkan."""


@dataclass
class PurgeResult:
    """Apa yang benar-benar terhapus."""

    statistic_ids: list[str]
    cutoff: datetime
    long_term_rows: int = 0
    short_term_rows: int = 0

    @property
    def total_rows(self) -> int:
        """Seluruh baris yang dihapus."""
        return self.long_term_rows + self.short_term_rows

    def as_response(self) -> dict[str, Any]:
        """Ringkasan yang dikembalikan ke pemanggil layanan."""
        return {
            "entities": len(self.statistic_ids),
            "cutoff": self.cutoff.isoformat(),
            "long_term_rows": self.long_term_rows,
            "short_term_rows": self.short_term_rows,
            "total_rows": self.total_rows,
        }


def retention_days(keep_years: str | None) -> float | None:
    """Ubah pilihan retensi jadi jumlah hari. None berarti simpan selamanya."""
    if not keep_years or keep_years == RETENTION_UNLIMITED:
        return None
    try:
        years = float(keep_years)
    except (TypeError, ValueError):
        return None
    if years <= 0:
        return None
    return years * DAYS_PER_YEAR


def check_supported() -> None:
    """Pastikan struktur internal recorder masih seperti yang kita harapkan.

    Dipanggil sebelum penghapusan apa pun. Kalau Home Assistant mengubah nama
    model atau kolomnya, lebih baik gagal di sini dengan pesan yang jelas
    daripada menghapus baris yang salah.

    :raises RetentionUnsupportedError: bila struktur tidak dikenali.
    """
    try:
        from homeassistant.components.recorder.db_schema import (  # noqa: PLC0415
            Statistics,
            StatisticsMeta,
            StatisticsShortTerm,
        )
    except ImportError as err:
        raise RetentionUnsupportedError(
            "Model statistik recorder tidak ditemukan di versi Home Assistant ini"
        ) from err

    required = {
        Statistics: ("id", "metadata_id", "start_ts"),
        StatisticsShortTerm: ("id", "metadata_id", "start_ts"),
        StatisticsMeta: ("id", "statistic_id"),
    }
    for model, columns in required.items():
        available = {column.name for column in model.__table__.columns}
        missing = [column for column in columns if column not in available]
        if missing:
            raise RetentionUnsupportedError(
                f"Tabel {model.__tablename__} tidak punya kolom {missing} "
                "di versi Home Assistant ini"
            )


def _purge_in_recorder_thread(
    hass: HomeAssistant, statistic_ids: list[str], cutoff: datetime
) -> PurgeResult:
    """Hapus baris statistik lama. **Dijalankan di thread recorder.**"""
    from homeassistant.components.recorder.db_schema import (  # noqa: PLC0415
        Statistics,
        StatisticsMeta,
        StatisticsShortTerm,
    )
    from homeassistant.components.recorder.util import (  # noqa: PLC0415
        session_scope,
    )

    result = PurgeResult(statistic_ids=list(statistic_ids), cutoff=cutoff)
    cutoff_ts = cutoff.timestamp()

    with session_scope(hass=hass) as session:
        metadata_ids = [
            row.id
            for row in session.query(StatisticsMeta.id)
            .filter(StatisticsMeta.statistic_id.in_(statistic_ids))
            .all()
        ]

    if not metadata_ids:
        return result

    for model, field in (
        (Statistics, "long_term_rows"),
        (StatisticsShortTerm, "short_term_rows"),
    ):
        deleted = 0
        while True:
            # Sengaja satu sesi per batch: tiap commit melepas write-lock,
            # jadi kompaksi per jam milik recorder tidak tertahan lama.
            with session_scope(hass=hass) as session:
                row_ids = [
                    row.id
                    for row in session.query(model.id)
                    .filter(
                        model.metadata_id.in_(metadata_ids),
                        model.start_ts < cutoff_ts,
                    )
                    .limit(MAX_ROWS_PER_BATCH)
                    .all()
                ]
                if not row_ids:
                    break
                session.query(model).filter(model.id.in_(row_ids)).delete(
                    synchronize_session=False
                )
                deleted += len(row_ids)

        setattr(result, field, deleted)

    # Baris StatisticsMeta sengaja TIDAK disentuh: menghapusnya akan memicu
    # cascade yang membuang seluruh riwayat entity, persis yang ingin dihindari.
    return result


async def async_purge_statistics(
    hass: HomeAssistant, statistic_ids: list[str], cutoff: datetime
) -> PurgeResult:
    """Hapus statistik milik entity kita yang lebih tua dari ``cutoff``.

    :raises RetentionUnsupportedError: bila recorder tidak aktif atau struktur
        internalnya sudah berubah.
    """
    if "recorder" not in hass.config.components:
        raise RetentionUnsupportedError("Recorder tidak aktif di instalasi ini")

    check_supported()

    if not statistic_ids:
        return PurgeResult(statistic_ids=[], cutoff=cutoff)

    from homeassistant.components.recorder import get_instance  # noqa: PLC0415

    instance = get_instance(hass)
    _LOGGER.info(
        "Menghapus statistik %s entity milik integrasi ini yang lebih tua dari %s",
        len(statistic_ids),
        cutoff.isoformat(),
    )

    # Wajib dijalankan di thread recorder: operasi tulis ke database recorder
    # tidak aman dipanggil dari thread lain.
    result = await instance.async_add_executor_job(
        _purge_in_recorder_thread, hass, statistic_ids, cutoff
    )

    _LOGGER.info(
        "Selesai: %s baris statistik jangka panjang dan %s baris jangka pendek dihapus",
        result.long_term_rows,
        result.short_term_rows,
    )
    return result
