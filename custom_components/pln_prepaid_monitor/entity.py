"""Kelas dasar entity milik integrasi ini."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import (
    ATTR_HOLDING_LAST_VALUE,
    ATTR_SOURCE_OF_TRUTH,
    ATTR_UNAVAILABLE_SINCE,
    DOMAIN,
)
from .coordinator import SourceRuntime


class PlnSourceEntity(Entity):
    """Entity yang mengikuti satu Energy Source."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, runtime: SourceRuntime, key: str) -> None:
        """Ikat entity ke runtime source dan beri identitas yang stabil."""
        self._runtime = runtime
        self._key = key
        self._attr_unique_id = f"{runtime.subentry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.subentry_id)},
            name=runtime.name,
            manufacturer="PLN Prepaid Monitor",
            model="Energy Source",
        )

    async def async_added_to_hass(self) -> None:
        """Berlangganan perubahan dari runtime."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._runtime.async_add_listener(self.async_write_ha_state)
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Atribut yang berlaku untuk semua entity satu source."""
        return {
            ATTR_SOURCE_OF_TRUTH: self._runtime.source_of_truth,
            ATTR_HOLDING_LAST_VALUE: self._runtime.holding_last_value,
            ATTR_UNAVAILABLE_SINCE: self._runtime.unavailable_since,
        }
