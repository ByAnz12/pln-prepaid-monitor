"""Jaminan bahwa integrasi ini tidak pernah bisa mengendalikan listrik.

Ini aturan non-negotiable dari spec (Executive Summary + O.4). Test di sini
sengaja dibuat kasar dan menyeluruh supaya siapa pun yang nanti menambah kode
baru akan langsung tersandung kalau melanggarnya - termasuk saya sendiri di
milestone berikutnya.
"""

from __future__ import annotations

from pathlib import Path

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pln_prepaid_monitor.const import (
    CONF_ENERGY_ENTITY_ID,
    CONF_POWER_ENTITY_ID,
    DOMAIN,
    PLATFORMS,
    SUBENTRY_TYPE_ENERGY_SOURCE,
)

from .conftest import apply_states, MCB_RUMAH, RELAY_ENTITIES

INTEGRATION_DIR = (
    Path(__file__).parent.parent / "custom_components" / "pln_prepaid_monitor"
)

CONTROL_PLATFORMS = {
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.FAN,
    Platform.LIGHT,
    Platform.LOCK,
    Platform.SIREN,
    Platform.VALVE,
    Platform.WATER_HEATER,
}


def test_no_control_platform_is_registered() -> None:
    """Daftar platform tidak boleh memuat satu pun platform yang bisa mengontrol."""
    assert set(PLATFORMS) == {Platform.SENSOR, Platform.BINARY_SENSOR}
    assert not CONTROL_PLATFORMS & set(PLATFORMS)


def test_no_control_platform_module_exists() -> None:
    """Tidak boleh ada file platform kontrol di dalam paket integrasi."""
    for platform in CONTROL_PLATFORMS:
        assert not (INTEGRATION_DIR / f"{platform.value}.py").exists()


def test_source_never_calls_a_service() -> None:
    """Tidak ada satu pun pemanggilan service di seluruh kode integrasi."""
    forbidden = (
        "services.async_call",
        "services.call",
        "async_call_service",
        "turn_on",
        "turn_off",
        "toggle",
    )
    offenders: list[str] = []
    for path in INTEGRATION_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            if needle in text:
                offenders.append(f"{path.name}: {needle}")
    assert offenders == []


async def test_relay_entities_are_untouched_after_setup(
    hass: HomeAssistant,
) -> None:
    """State entity relay/breaker milik user harus persis sama sebelum & sesudah."""
    apply_states(hass, MCB_RUMAH, RELAY_ENTITIES)
    before = {
        entity_id: hass.states.get(entity_id).state for entity_id in RELAY_ENTITIES
    }

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        subentries_data=[
            {
                "data": {
                    "name": "MCB RUMAH",
                    CONF_ENERGY_ENTITY_ID: "sensor.mcb_rumah_total_energy",
                    CONF_POWER_ENTITY_ID: "sensor.mcb_rumah_phase_a_power",
                },
                "subentry_type": SUBENTRY_TYPE_ENERGY_SOURCE,
                "title": "MCB RUMAH",
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Pancing banyak perubahan state, termasuk reset counter.
    attributes = MCB_RUMAH["sensor.mcb_rumah_total_energy"][1]
    for value in ("15500", "15600", "0.1", "5"):
        hass.states.async_set("sensor.mcb_rumah_total_energy", value, attributes)
        await hass.async_block_till_done()

    after = {
        entity_id: hass.states.get(entity_id).state for entity_id in RELAY_ENTITIES
    }
    assert after == before


async def test_only_bookkeeping_services_are_registered(
    hass: HomeAssistant,
) -> None:
    """Daftar layanan dikunci: semuanya hanya mengubah catatan token.

    Tidak ada satu pun yang menyentuh perangkat. Kalau suatu saat ada layanan
    baru yang ditambahkan, test ini akan gagal dan memaksa keputusan itu ditinjau
    ulang secara sadar.
    """
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registered = set(hass.services.async_services().get(DOMAIN, {}))

    assert registered == {
        "add_token_topup",
        "calibrate_token_reading",
        "edit_topup",
        "delete_topup",
        "reset_token_ledger",
        "resolve_ledger_hold",
    }
    for name in registered:
        assert not any(
            word in name for word in ("turn", "switch", "toggle", "power", "breaker")
        )
