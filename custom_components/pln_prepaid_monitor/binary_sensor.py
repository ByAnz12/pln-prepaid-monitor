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

from .const import (
    ATTR_CONFIDENCE,
    ATTR_DATA_POINTS,
    ATTR_HOLD_RESET_FROM,
    ATTR_HOLD_RESET_TO,
    ATTR_HOLD_SINCE,
    ATTR_HOLD_SOURCE,
    ATTR_WINDOW_USED,
)
from .coordinator import BillingGroupRuntime, PlnRuntimeData, SourceRuntime
from .entity import PlnBillingGroupEntity, PlnSourceEntity

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

    for subentry_id, group in runtime_data.billing_groups.items():
        entities: list[BinarySensorEntity] = [
            PlnGroupDataSufficientBinarySensor(group)
        ]
        if group.token_enabled:
            entities.append(PlnGroupLedgerHoldBinarySensor(group))
        async_add_entities(entities, config_subentry_id=subentry_id)


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


class PlnGroupLedgerHoldBinarySensor(PlnBillingGroupEntity, BinarySensorEntity):
    """Menyala saat pencatatan token dibekukan menunggu keputusan Anda.

    Ini terjadi kalau meteran ter-reset dan pembacaan pertama sesudahnya cukup
    besar untuk merusak hitungan sisa token. Daripada diam-diam memotong sisa
    token Anda, sistem berhenti dan bertanya lebih dulu - lihat
    docs/decisions.md D-007.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, group: BillingGroupRuntime) -> None:
        """Siapkan sensor penahanan ledger."""
        super().__init__(group, "ledger_hold")

    @property
    def is_on(self) -> bool:
        """True selama ledger masih ditahan."""
        return self._group.ledger.on_hold

    @property
    def extra_state_attributes(self) -> dict:
        """Detail reset yang memicu penahanan, supaya user bisa memutuskan."""
        attributes = super().extra_state_attributes
        hold = self._group.ledger.state.hold or {}
        attributes.update(
            {
                ATTR_HOLD_SINCE: hold.get("since"),
                ATTR_HOLD_SOURCE: hold.get("source_name"),
                ATTR_HOLD_RESET_FROM: hold.get("reset_from"),
                ATTR_HOLD_RESET_TO: hold.get("reset_to"),
            }
        )
        return attributes


class PlnGroupDataSufficientBinarySensor(PlnBillingGroupEntity, BinarySensorEntity):
    """Menyala saat data pemakaian sudah cukup untuk membuat perkiraan.

    Dipakai dashboard sebagai gerbang: selama ini masih mati, angka perkiraan
    memang belum ada, dan itu disengaja - lebih baik menampilkan "sedang
    mengumpulkan data" daripada tanggal yang ditebak dari dua hari pemakaian.
    """

    def __init__(self, group: BillingGroupRuntime) -> None:
        """Siapkan sensor kecukupan data."""
        super().__init__(group, "data_sufficient")

    @property
    def is_on(self) -> bool:
        """True bila perkiraan sudah punya dasar yang layak."""
        return self._group.prediction.data_sufficient

    @property
    def extra_state_attributes(self) -> dict:
        """Berapa titik data yang sudah terkumpul, dari rentang mana."""
        prediction = self._group.prediction
        attributes = super().extra_state_attributes
        attributes.update(
            {
                ATTR_WINDOW_USED: prediction.window_used,
                ATTR_DATA_POINTS: prediction.data_points,
                ATTR_CONFIDENCE: prediction.confidence,
            }
        )
        return attributes
