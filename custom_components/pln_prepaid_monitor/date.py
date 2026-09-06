"""Batas rentang tabel riwayat: dari tanggal, sampai tanggal.

Seperti ``number`` dan ``text``, entity di sini **tidak mengendalikan perangkat
apa pun**: ia hanya menyimpan tanggal milik integrasi ini sendiri. Jaminannya
diuji lewat perilaku di ``tests/test_readonly_guarantee.py``, bukan lewat nama
platform - lihat D-039 dan D-047.

Kenapa ``date`` dan bukan kotak teks: rentang yang diketik tangan mengundang
salah tulis, dan tanggal yang gagal dibaca menghasilkan tabel kosong tanpa
penjelasan. Platform ``date`` bawaan Home Assistant menampilkan pemilih tanggal
sungguhan, jadi tidak ada format yang perlu dihafal user.
"""

from __future__ import annotations

from datetime import date

from homeassistant.components.date import DateEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BillingGroupRuntime, PlnRuntimeData
from .entity import PlnBillingGroupEntity

# Kunci isian, dan berapa hari sebelum hari ini nilai bawaannya jatuh.
RANGE_INPUTS: tuple[tuple[str, int], ...] = (
    ("usage_from", 30),
    ("usage_to", 0),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Buat dua batas rentang untuk tiap kelompok tagihan."""
    runtime_data: PlnRuntimeData = entry.runtime_data
    for subentry_id, group in runtime_data.billing_groups.items():
        async_add_entities(
            [PlnUsageRangeDate(group, key, days_ago) for key, days_ago in RANGE_INPUTS],
            config_subentry_id=subentry_id,
        )


class PlnUsageRangeDate(PlnBillingGroupEntity, DateEntity):
    """Satu batas rentang tabel riwayat."""

    def __init__(self, group: BillingGroupRuntime, key: str, days_ago: int) -> None:
        """Siapkan pemilih tanggal."""
        super().__init__(group, key)
        self._days_ago = days_ago

    @property
    def native_value(self) -> date | None:
        """Tanggal yang tersimpan, atau nilai bawaan kalau belum pernah diisi."""
        query = self._group.usage_query
        return query.start if self._key == "usage_from" else query.end

    async def async_set_value(self, value: date) -> None:
        """Simpan tanggal baru, lalu susun ulang tabelnya."""
        self._group.async_set_usage_control(self._key, value.isoformat())
