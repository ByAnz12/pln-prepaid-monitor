"""Isian nama template pengisian.

Seperti platform isian lainnya, entity ini **tidak mengendalikan perangkat apa
pun** - ia hanya menampung nama sampai tombol simpan ditekan.
"""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BillingGroupRuntime, PlnRuntimeData
from .entity import PlnBillingGroupEntity

# Cukup untuk "Beli besar bulanan" tanpa membuat tombolnya terlalu lebar.
MAX_NAME_LENGTH = 32


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Buat isian nama template untuk kelompok tagihan yang mencatat token."""
    runtime_data: PlnRuntimeData = entry.runtime_data
    for subentry_id, group in runtime_data.billing_groups.items():
        if not group.token_enabled:
            continue
        async_add_entities(
            [PlnTemplateNameText(group)], config_subentry_id=subentry_id
        )


class PlnTemplateNameText(PlnBillingGroupEntity, TextEntity):
    """Nama untuk template yang akan disimpan.

    Boleh dikosongkan: template tanpa nama tetap tersimpan, hanya labelnya
    memakai angkanya sendiri.
    """

    _attr_native_min = 0
    _attr_native_max = MAX_NAME_LENGTH
    _attr_mode = TextMode.TEXT

    def __init__(self, group: BillingGroupRuntime) -> None:
        """Siapkan isian nama template."""
        super().__init__(group, "template_name")

    @property
    def native_value(self) -> str:
        """Nama yang sedang diketik, belum tersimpan ke mana pun."""
        return self._group.inputs_text.get(self._key, "")

    async def async_set_value(self, value: str) -> None:
        """Simpan namanya saja - template baru tercatat saat tombol ditekan."""
        self._group.async_set_input_text(self._key, value[:MAX_NAME_LENGTH])
