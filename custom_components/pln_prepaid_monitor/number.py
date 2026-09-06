"""Isian angka yang bisa diubah langsung dari dashboard.

Entity di sini **tidak mengendalikan perangkat apa pun**. Yang disentuhnya
hanya catatan token dan pengaturan integrasi ini sendiri - tidak ada satu pun
yang bisa memutus atau menyalakan listrik. Jaminan itu diuji di
``tests/test_readonly_guarantee.py``.

Ada dua jenis yang perilakunya berbeda, dan bedanya penting:

* **Isian sementara** (jumlah kWh pengisian, angka di layar meteran) hanya
  menampung angka sampai tombolnya ditekan. Nilainya dipulihkan setelah restart
  supaya tidak hilang, tapi tidak pernah masuk ke konfigurasi.
* **Pengaturan** (ambang peringatan, tarif per kWh) menulis ke konfigurasi.
  Karena itu mengubahnya memuat ulang integrasi sebentar - persis seperti
  mengubahnya lewat layar Configure.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CRITICAL_THRESHOLD_DAYS,
    CONF_RATE_HISTORY,
    CONF_RATE_RP_PER_KWH,
    CONF_TARIFF_ID,
    CONF_VERY_CRITICAL_THRESHOLD_DAYS,
    CONF_WARNING_THRESHOLD_DAYS,
    DOMAIN,
    SUBENTRY_TYPE_TARIFF,
)
from .coordinator import BillingGroupRuntime, PlnRuntimeData
from .engines.usage_table import DEFAULT_MAX_ROWS, MAX_ROWS_LIMIT
from .engines.cost_engine import append_rate_version
from .engines.token_engine import MAX_PLAUSIBLE_TOPUP_KWH
from .entity import PlnBillingGroupEntity, PlnTariffEntity

# Batas atas isian kWh disamakan dengan ambang kewajaran yang sudah dipakai saat
# mencatat pengisian, supaya angka satuan KWM dari struk (82650) tidak bisa
# masuk lewat pintu ini.
MAX_KWH = MAX_PLAUSIBLE_TOPUP_KWH

# Tarif listrik Indonesia berada di kisaran ribuan rupiah per kWh. Batas ini
# longgar, hanya untuk mencegah salah ketik yang ekstrem.
MAX_RATE_RP = 100_000.0

# Cukup untuk melihat beberapa pembelian terakhir tanpa kartunya jadi panjang.
DEFAULT_HISTORY_ROWS = 10.0

# Batas atas nominal pembelian. Longgar, sekadar mencegah salah ketik yang
# ekstrem - bukan pernyataan tentang berapa yang wajar dibeli orang.
MAX_NOMINAL_RP = 1_000_000_000.0

# (kunci konfigurasi, nama field di TokenThresholds, translation_key entity)
THRESHOLD_KEYS = (
    (CONF_WARNING_THRESHOLD_DAYS, "warning_days", "warning_threshold_days"),
    (CONF_CRITICAL_THRESHOLD_DAYS, "critical_days", "critical_threshold_days"),
    (
        CONF_VERY_CRITICAL_THRESHOLD_DAYS,
        "very_critical_days",
        "very_critical_threshold_days",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Buat isian angka untuk kelompok tagihan dan tarif."""
    runtime_data: PlnRuntimeData = entry.runtime_data

    for subentry_id, group in runtime_data.billing_groups.items():
        # Berlaku untuk semua kelompok: tabel pemakaian tidak ada hubungannya
        # dengan token.
        async_add_entities(
            [PlnUsageRowsNumber(group)], config_subentry_id=subentry_id
        )
        if not group.token_enabled:
            continue
        entities: list[NumberEntity] = [
            PlnTopupAmountNumber(group),
            PlnTopupNominalNumber(hass, group),
            PlnMeterReadingNumber(group),
            PlnHistoryRowsNumber(group),
        ]
        entities.extend(
            PlnThresholdNumber(hass, entry, group, conf_key, field, key)
            for conf_key, field, key in THRESHOLD_KEYS
        )
        async_add_entities(entities, config_subentry_id=subentry_id)

    # Satu isian tarif per subentry tarif, bukan per kelompok - lihat
    # PlnTariffEntity untuk alasannya.
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_TARIFF:
            continue
        async_add_entities(
            [PlnTariffRateNumber(hass, entry, subentry_id)],
            config_subentry_id=subentry_id,
        )


class _PlnInputNumber(PlnBillingGroupEntity, NumberEntity):
    """Isian sementara: menampung angka sampai tombolnya ditekan.

    Angkanya disimpan di runtime kelompok tagihan, bukan di entity ini, supaya
    tombol di sebelahnya membaca angka yang sama persis - dan supaya angkanya
    ikut tersimpan bersama state lain kalau Home Assistant restart.
    """

    _attr_native_min_value = 0.0
    _attr_native_max_value = MAX_KWH
    _attr_native_step = 0.01
    _attr_native_unit_of_measurement = "kWh"
    _attr_mode = NumberMode.BOX

    @property
    def native_value(self) -> float:
        """Angka yang sedang tersimpan untuk isian ini."""
        return self._group.inputs.get(self._key, 0.0)

    async def async_set_native_value(self, value: float) -> None:
        """Simpan angkanya saja - tidak ada yang tercatat sampai tombol ditekan."""
        self._group.async_set_input(self._key, value)


class PlnTopupAmountNumber(_PlnInputNumber):
    """Berapa kWh yang masuk pada pengisian yang akan dicatat."""

    def __init__(self, group: BillingGroupRuntime) -> None:
        """Buat isian jumlah kWh pengisian."""
        super().__init__(group, "topup_kwh")


class PlnTopupNominalNumber(PlnBillingGroupEntity, NumberEntity):
    """Nominal pembelian, untuk yang lebih hafal harga daripada jumlah kWh.

    Boleh diisi sendirian - kWh-nya dihitung dari tarif - atau bersama kotak
    kWh, yang justru paling tepat karena keduanya tertulis di struk.
    """

    _attr_native_min_value = 0.0
    _attr_native_max_value = MAX_NOMINAL_RP
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, hass: HomeAssistant, group: BillingGroupRuntime) -> None:
        """Buat isian nominal pembelian, bersatuan mata uang Home Assistant."""
        super().__init__(group, "topup_rp")
        self._attr_native_unit_of_measurement = hass.config.currency or None

    @property
    def native_value(self) -> float:
        """Angka yang sedang tersimpan untuk isian ini."""
        return self._group.inputs.get(self._key, 0.0)

    async def async_set_native_value(self, value: float) -> None:
        """Simpan angkanya saja - tidak ada yang tercatat sampai tombol ditekan."""
        self._group.async_set_input(self._key, value)


class PlnMeterReadingNumber(_PlnInputNumber):
    """Sisa token yang tertera di layar meteran fisik, untuk penyamaan."""

    def __init__(self, group: BillingGroupRuntime) -> None:
        """Buat isian angka layar meteran."""
        super().__init__(group, "meter_reading_kwh")


class PlnHistoryRowsNumber(PlnBillingGroupEntity, NumberEntity):
    """Berapa baris riwayat pengisian yang ditampilkan di dashboard.

    Dibuat sebagai isian bebas, bukan pilihan 5/10/20: begitu ada kotak angka,
    angka berapa pun bisa dipakai, dan tidak ada alasan membatasi user pada tiga
    nilai yang kebetulan saya pilih.
    """

    _attr_native_min_value = 1
    _attr_native_max_value = 50
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, group: BillingGroupRuntime) -> None:
        """Buat isian jumlah baris riwayat."""
        super().__init__(group, "history_rows")

    @property
    def native_value(self) -> float:
        """Jumlah baris yang sedang dipilih, bawaannya 10."""
        return self._group.inputs.get(self._key, DEFAULT_HISTORY_ROWS)

    async def async_set_native_value(self, value: float) -> None:
        """Simpan pilihannya; kartu riwayat membacanya lewat template."""
        self._group.async_set_input(self._key, value)


class PlnThresholdNumber(PlnBillingGroupEntity, NumberEntity):
    """Ambang berapa hari tersisa sebelum status token berubah.

    Menulis ke konfigurasi kelompok tagihan, jadi mengubahnya memuat ulang
    integrasi sebentar - sama seperti mengubahnya lewat layar Configure.
    """

    _attr_native_min_value = 0.1
    _attr_native_max_value = 90.0
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "d"
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        group: BillingGroupRuntime,
        conf_key: str,
        field: str,
        key: str,
    ) -> None:
        """Ikat isian ke satu kunci konfigurasi ambang."""
        super().__init__(group, key)
        self._hass = hass
        self._entry = entry
        self._conf_key = conf_key
        self._field = field

    @property
    def native_value(self) -> float:
        """Ambil dari ambang yang sedang berlaku di runtime."""
        return float(getattr(self._group.thresholds, self._field))

    async def async_set_native_value(self, value: float) -> None:
        """Tolak urutan yang tidak masuk akal, jangan diam-diam diurutkan sendiri."""
        current = {
            conf_key: float(getattr(self._group.thresholds, field))
            for conf_key, field, _ in THRESHOLD_KEYS
        }
        current[self._conf_key] = value

        if not (
            current[CONF_WARNING_THRESHOLD_DAYS]
            > current[CONF_CRITICAL_THRESHOLD_DAYS]
            > current[CONF_VERY_CRITICAL_THRESHOLD_DAYS]
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="thresholds_out_of_order",
            )

        subentry = self._entry.subentries[self._group.subentry_id]
        self._hass.config_entries.async_update_subentry(
            self._entry,
            subentry,
            data={**subentry.data, self._conf_key: value},
        )


class PlnTariffRateNumber(PlnTariffEntity, NumberEntity):
    """Harga listrik per kWh.

    Perubahan tarif **menambah versi baru**, tidak pernah menimpa yang lama
    (spec K.7). Biaya yang sudah tercatat tetap memakai tarif yang berlaku saat
    pemakaian itu terjadi - kenaikan tarif tidak menulis ulang masa lalu.
    """

    _attr_native_min_value = 0.0
    _attr_native_max_value = MAX_RATE_RP
    _attr_native_step = 0.01
    _attr_mode = NumberMode.BOX

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, subentry_id: str) -> None:
        """Ikat isian ke satu subentry tarif."""
        data = entry.subentries[subentry_id].data
        super().__init__(
            subentry_id, str(data.get(CONF_NAME, "")) or subentry_id, "rate_rp_per_kwh"
        )
        self._hass = hass
        self._entry = entry
        self._attr_native_unit_of_measurement = (
            f"{hass.config.currency}/kWh" if hass.config.currency else None
        )

    @property
    def native_value(self) -> float:
        """Tarif yang sedang berlaku menurut konfigurasi."""
        data = self._entry.subentries[self._subentry_id].data
        return float(data.get(CONF_RATE_RP_PER_KWH, 0.0))

    async def async_set_native_value(self, value: float) -> None:
        """Simpan tarif baru sebagai versi berikutnya."""
        subentry = self._entry.subentries[self._subentry_id]
        self._hass.config_entries.async_update_subentry(
            self._entry,
            subentry,
            data={
                **subentry.data,
                CONF_RATE_RP_PER_KWH: value,
                CONF_RATE_HISTORY: append_rate_version(
                    list(subentry.data.get(CONF_RATE_HISTORY) or []),
                    value,
                    dt_util.now().isoformat(),
                ),
            },
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Kelompok tagihan mana saja yang memakai tarif ini."""
        runtime_data: PlnRuntimeData = self._entry.runtime_data
        return {
            "used_by": [
                group.name
                for group in runtime_data.billing_groups.values()
                if self._entry.subentries[group.subentry_id].data.get(CONF_TARIFF_ID)
                == self._subentry_id
            ]
        }


class PlnUsageRowsNumber(PlnBillingGroupEntity, NumberEntity):
    """Berapa baris tabel pemakaian yang ditampilkan sekaligus.

    Namanya sengaja dibedakan dari ``history_rows``, yang mengatur panjang
    daftar riwayat **pengisian token** - dua hal berbeda yang sama-sama berupa
    daftar, dan nama yang mirip akan tertukar cepat atau lambat.

    Ini pagar, bukan selera: tabel markdown 365 baris tidak terbaca siapa pun,
    dan atribut state yang lewat 16 KiB ditolak recorder.
    """

    _attr_native_min_value = 1
    _attr_native_max_value = MAX_ROWS_LIMIT
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, group: BillingGroupRuntime) -> None:
        """Buat isian jumlah baris tabel pemakaian."""
        super().__init__(group, "usage_rows")

    @property
    def native_value(self) -> float:
        """Jumlah baris yang sedang dipilih."""
        return self._group.inputs.get(self._key, DEFAULT_MAX_ROWS)

    async def async_set_native_value(self, value: float) -> None:
        """Simpan pilihannya, lalu susun ulang tabelnya."""
        self._group.async_set_usage_control(self._key, value)
