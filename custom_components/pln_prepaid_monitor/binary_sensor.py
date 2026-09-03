"""Binary sensor ketersediaan per Energy Source.

Bedanya dengan sekadar melihat entity aslinya: sensor ini menghormati masa
tenggang (grace period) yang diatur user. Gangguan singkat (WiFi/Zigbee drop
beberapa detik) tidak langsung dilaporkan sebagai sumber mati - lihat spec
K.1 vs K.2.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PlnRuntimeData, SourceRuntime
from .entity import PlnSourceEntity

CHANNEL_AVAILABLE = "available"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Buat satu binary sensor ketersediaan per Energy Source."""
    runtime_data: PlnRuntimeData = entry.runtime_data
    for subentry_id, runtime in runtime_data.sources.items():
        async_add_entities(
            [PlnSourceAvailableBinarySensor(runtime)],
            config_subentry_id=subentry_id,
        )


class PlnSourceAvailableBinarySensor(PlnSourceEntity, BinarySensorEntity):
    """Apakah sumber energi ini sedang terhubung dan mengirim data."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, runtime: SourceRuntime) -> None:
        """Siapkan sensor ketersediaan."""
        super().__init__(runtime, CHANNEL_AVAILABLE)

    @property
    def is_on(self) -> bool:
        """True bila sumber sehat atau masih dalam masa tenggang."""
        return self._runtime.available

    @property
    def available(self) -> bool:
        """Selalu tersedia: entity inilah yang melaporkan status sumber."""
        return True
