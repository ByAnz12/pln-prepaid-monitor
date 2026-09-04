"""Tombol pencatatan token, supaya tidak perlu lewat Developer Tools.

Sama seperti ``number.py``, entity di sini **tidak mengendalikan perangkat apa
pun** - yang disentuhnya hanya catatan token.

Yang sengaja **tidak** dibuat di sini: tombol reset ledger. Menekan tombol
entity langsung menjalankan aksinya tanpa dialog konfirmasi, sementara reset
menggantikan seluruh riwayat pengisian yang masih aktif dan tidak bisa
dibatalkan. Reset tetap tersedia sebagai tombol di dashboard, yang meminta
konfirmasi lebih dulu. Dua aksi di bawah ini masih bisa diperbaiki kalau
salah tekan - pengisian lewat **Perbaiki pengisian** atau **Hapus pengisian**,
penyamaan cukup dengan menyamakan ulang.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import BillingGroupRuntime, PlnRuntimeData
from .entity import PlnBillingGroupEntity

TOPUP_INPUT = "topup_kwh"
METER_INPUT = "meter_reading_kwh"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Buat tombol pencatatan untuk tiap kelompok tagihan yang mencatat token."""
    runtime_data: PlnRuntimeData = entry.runtime_data
    for subentry_id, group in runtime_data.billing_groups.items():
        if not group.token_enabled:
            continue
        async_add_entities(
            [PlnRecordTopupButton(group), PlnCalibrateButton(group)],
            config_subentry_id=subentry_id,
        )


class _PlnLedgerButton(PlnBillingGroupEntity, ButtonEntity):
    """Tombol yang membaca angka dari isian di sebelahnya."""

    def _amount(self, input_key: str) -> float:
        """Ambil angka dari isian, dan tolak kalau masih nol.

        Menekan tombol tanpa mengisi angka lebih mungkin berarti user lupa
        daripada benar-benar ingin mencatat nol - jadi ditolak dengan pesan,
        bukan mencatat nol diam-diam.
        """
        value = self._group.inputs.get(input_key, 0.0)
        if value <= 0:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="amount_not_set",
            )
        return value

    def _clear(self, input_key: str) -> None:
        """Kosongkan isian sesudah dipakai, supaya tidak tercatat dua kali."""
        self._group.async_set_input(input_key, 0.0)


class PlnRecordTopupButton(_PlnLedgerButton):
    """Catat pengisian sebanyak angka pada isian jumlah kWh."""

    def __init__(self, group: BillingGroupRuntime) -> None:
        """Buat tombol pencatatan pengisian."""
        super().__init__(group, "record_topup")

    async def async_press(self) -> None:
        """Catat pengisiannya, lalu kosongkan isiannya."""
        self._group.record_topup(kwh_credited=self._amount(TOPUP_INPUT))
        self._clear(TOPUP_INPUT)


class PlnCalibrateButton(_PlnLedgerButton):
    """Samakan sisa token dengan angka di layar meteran fisik."""

    def __init__(self, group: BillingGroupRuntime) -> None:
        """Buat tombol penyamaan."""
        super().__init__(group, "calibrate_token")

    async def async_press(self) -> None:
        """Samakan ledger, lalu kosongkan isiannya."""
        self._group.calibrate_to(actual_remaining_kwh=self._amount(METER_INPUT))
        self._clear(METER_INPUT)
