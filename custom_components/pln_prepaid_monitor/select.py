"""Pemilih template pengisian token.

Seperti ``number`` dan ``button``, entity di sini **tidak mengendalikan
perangkat apa pun**: ia hanya memilih salah satu template milik integrasi ini
sendiri, lalu mengisikan angkanya ke kotak isian. Jaminannya diuji lewat
perilaku di ``tests/test_readonly_guarantee.py``, bukan lewat nama platform.

Kenapa ``select``, bukan tombol per template seperti sebelumnya: tombol di
dashboard adalah YAML statis, jadi template yang baru disimpan tidak muncul
sampai dashboardnya dibuat ulang. Daftar pada ``select`` dibaca hidup-hidup,
jadi template yang baru disimpan langsung bisa dipakai.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BillingGroupRuntime, PlnRuntimeData
from .engines.usage_table import DIRECTIONS, GRAINS, SORTS, finer_grains
from .entity import PlnBillingGroupEntity

# Ditampilkan saat belum ada template sama sekali. Daftar pilihan yang benar-
# benar kosong membuat kartunya terlihat rusak.
#
# Nilainya harus berupa slug ``[a-z0-9-_]+`` yang tidak diawali atau diakhiri
# tanda hubung: hassfest memakainya sebagai kunci terjemahan di
# ``entity.select.topup_template.state``. Tanda hubung tunggal yang dipakai
# sebelumnya membuat seluruh strings.json ditolak. Yang dilihat user tetap
# kalimat utuh dari terjemahan, bukan nilai mentah ini.
NONE_OPTION = "no_template"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Buat pemilih template untuk kelompok tagihan yang mencatat token."""
    runtime_data: PlnRuntimeData = entry.runtime_data
    for subentry_id, group in runtime_data.billing_groups.items():
        entities: list[SelectEntity] = [
            PlnUsageTableSelect(group, key, choices) for key, choices in USAGE_SELECTS
        ]
        # Pemilih template hanya berguna kalau kelompok ini mencatat token;
        # kendali tabel riwayat berguna untuk semua kelompok.
        if group.token_enabled:
            entities.append(PlnTopupTemplateSelect(group))
        async_add_entities(entities, config_subentry_id=subentry_id)


class PlnTopupTemplateSelect(PlnBillingGroupEntity, SelectEntity):
    """Pilih template, dan kotak isiannya langsung terisi.

    Sengaja hanya **mengisi** kotaknya, tidak langsung mencatat: memilih dari
    daftar tidak punya dialog konfirmasi, sementara mencatat pengisian mengubah
    ledger. Dengan mengisi kotaknya dulu, angkanya terlihat sebelum Anda
    menekan **Catat pengisian**.
    """

    def __init__(self, group: BillingGroupRuntime) -> None:
        """Siapkan pemilih template."""
        super().__init__(group, "topup_template")

    @property
    def options(self) -> list[str]:
        """Nama template yang tersedia sekarang."""
        labels = [preset.label for preset in self._group.token_presets]
        return [NONE_OPTION, *labels] if labels else [NONE_OPTION]

    @property
    def current_option(self) -> str:
        """Template yang sedang dipilih, atau penanda kosong."""
        chosen = self._group.inputs_text.get(self._key)
        return chosen if chosen in self.options else NONE_OPTION

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Angka di balik tiap nama, supaya template bisa dibaca tanpa memilih."""
        attributes = super().extra_state_attributes
        attributes["templates"] = [
            {"name": preset.label, "detail": preset.detail, "kwh": preset.kwh,
             "nominal_rp": preset.nominal_rp}
            for preset in self._group.token_presets
        ]
        return attributes

    async def async_select_option(self, option: str) -> None:
        """Isikan angka template ke kotak jumlah kWh dan nominal."""
        self._group.async_set_input_text(self._key, option)
        if option == NONE_OPTION:
            return
        for preset in self._group.token_presets:
            if preset.label != option:
                continue
            self._group.async_set_input("topup_kwh", preset.kwh)
            self._group.async_set_input(
                "topup_rp", preset.nominal_rp if preset.nominal_rp else 0.0
            )
            # Namanya ikut terisi supaya mengubah nama terasa wajar: pilih,
            # perbaiki apa pun yang perlu, lalu perbarui.
            self._group.async_set_input_text("template_name", preset.name or "")
            return


# Kendali tabel riwayat. Semuanya hanya memilih cara menampilkan data yang
# sudah ada - tidak satu pun menyentuh perangkat.
#
# Tampilan sengaja tidak punya daftar tetap: pilihannya menyusut mengikuti
# jenis waktu, karena tampilan yang lebih kasar daripada rentangnya cuma
# menghasilkan satu baris gabungan yang tidak menjawab apa pun.
USAGE_SELECTS: tuple[tuple[str, tuple[str, ...] | None], ...] = (
    ("usage_scope", GRAINS),
    ("usage_view", None),
    ("usage_sort", SORTS),
    ("usage_direction", DIRECTIONS),
)


class PlnUsageTableSelect(PlnBillingGroupEntity, SelectEntity):
    """Satu kendali tabel riwayat."""

    def __init__(
        self, group: BillingGroupRuntime, key: str, choices: tuple[str, ...] | None
    ) -> None:
        """Siapkan pemilih; ``choices`` None berarti daftarnya dihitung hidup."""
        super().__init__(group, key)
        self._choices = choices

    @property
    def options(self) -> list[str]:
        """Pilihan yang tersedia sekarang."""
        if self._choices is not None:
            return list(self._choices)
        return finer_grains(self._group.usage_query.scope)

    @property
    def current_option(self) -> str:
        """Pilihan yang sedang berlaku, sudah dikoreksi kalau tidak sah lagi."""
        query = self._group.usage_query
        chosen = {
            "usage_scope": query.scope,
            "usage_view": query.view,
            "usage_sort": query.sort,
            "usage_direction": query.direction,
        }[self._key]
        return chosen if chosen in self.options else self.options[0]

    async def async_select_option(self, option: str) -> None:
        """Simpan pilihan, lalu susun ulang tabelnya."""
        self._group.async_set_usage_control(self._key, option)
