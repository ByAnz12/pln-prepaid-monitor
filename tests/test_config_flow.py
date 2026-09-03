"""Test config flow dan subentry flow memakai entity nyata MCB RUMAH & MCB TOKO."""

from __future__ import annotations

from typing import Any

import pytest
import voluptuous as vol
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.selector import EntitySelector
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pln_prepaid_monitor.const import (
    CONF_CURRENT_ENTITY_ID,
    CONF_ENERGY_ENTITY_ID,
    CONF_FREQUENCY_ENTITY_ID,
    CONF_POWER_ENTITY_ID,
    CONF_VOLTAGE_ENTITY_ID,
    DOMAIN,
    SUBENTRY_TYPE_ENERGY_SOURCE,
)

from .conftest import apply_states, MCB_RUMAH, MCB_TOKO, RELAY_ENTITIES

MCB_RUMAH_MAPPING = {
    "name": "MCB RUMAH",
    CONF_ENERGY_ENTITY_ID: "sensor.mcb_rumah_total_energy",
    CONF_POWER_ENTITY_ID: "sensor.mcb_rumah_phase_a_power",
    CONF_VOLTAGE_ENTITY_ID: "sensor.mcb_rumah_phase_a_voltage",
    CONF_CURRENT_ENTITY_ID: "sensor.mcb_rumah_phase_a_current",
    CONF_FREQUENCY_ENTITY_ID: "sensor.mcb_rumah_supply_frequency",
}

MCB_TOKO_MAPPING = {
    "name": "MCB TOKO",
    CONF_ENERGY_ENTITY_ID: "sensor.mcb_toko_total_energy",
    CONF_POWER_ENTITY_ID: "sensor.mcb_toko_phase_a_power",
    CONF_VOLTAGE_ENTITY_ID: "sensor.mcb_toko_phase_a_voltage",
    CONF_CURRENT_ENTITY_ID: "sensor.mcb_toko_phase_a_current",
    CONF_FREQUENCY_ENTITY_ID: "sensor.mcb_toko_supply_frequency",
}


def _selector_domains(schema: vol.Schema) -> set[str]:
    """Kumpulkan semua domain yang boleh dipilih di sebuah form."""
    domains: set[str] = set()
    for selector in schema.schema.values():
        if not isinstance(selector, EntitySelector):
            continue
        for entity_filter in selector.config.get("filter", []):
            domain = entity_filter.get("domain")
            if isinstance(domain, str):
                domains.add(domain)
            elif domain:
                domains.update(domain)
    return domains


async def _walk_to_map_entities(hass: HomeAssistant) -> dict[str, Any]:
    """Jalankan config flow sampai berhenti di langkah pemetaan sensor."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"add_source_now": True}
    )
    assert result["step_id"] == "pick_device"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"show_all_sensors": False}
    )
    assert result["step_id"] == "map_entities"
    return result


async def test_full_flow_creates_entry_with_first_source(
    hass: HomeAssistant,
) -> None:
    """Setup pertama kali langsung menghasilkan Energy Source MCB RUMAH."""
    apply_states(hass, MCB_RUMAH, RELAY_ENTITIES)

    result = await _walk_to_map_entities(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], dict(MCB_RUMAH_MAPPING)
    )

    assert result["step_id"] == "review"
    summary = result["description_placeholders"]["summary"]
    assert "MCB RUMAH" in summary
    assert "sensor.mcb_rumah_total_energy" in summary
    # Konversi kW -> W harus diberitahukan ke user, bukan dilakukan diam-diam.
    assert "kW" in summary

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert len(entry.subentries) == 1
    subentry = next(iter(entry.subentries.values()))
    assert subentry.subentry_type == SUBENTRY_TYPE_ENERGY_SOURCE
    assert subentry.title == "MCB RUMAH"
    assert subentry.data[CONF_ENERGY_ENTITY_ID] == "sensor.mcb_rumah_total_energy"


async def test_flow_can_skip_first_source(hass: HomeAssistant) -> None:
    """User boleh memasang integrasi dulu, mengatur sumbernya nanti."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"add_source_now": False}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert hass.config_entries.async_entries(DOMAIN)[0].subentries == {}


async def test_only_one_instance_allowed(hass: HomeAssistant) -> None:
    """Integrasi ini hanya boleh dipasang sekali; source ditambah lewat subentry."""
    MockConfigEntry(domain=DOMAIN, data={}).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_switch_and_number_domains_are_never_offered(
    hass: HomeAssistant,
) -> None:
    """Entity relay/breaker/threshold mustahil dipilih karena domainnya disaring.

    Ini jaminan struktural untuk daftar entity di spec O.4 - berlaku juga untuk
    perangkat yang baru ditambahkan user di kemudian hari, bukan hanya untuk
    entity_id yang kebetulan sudah diketahui hari ini.
    """
    apply_states(hass, MCB_RUMAH, RELAY_ENTITIES)

    result = await _walk_to_map_entities(hass)
    domains = _selector_domains(result["data_schema"])

    assert domains <= {"sensor", "binary_sensor"}
    for forbidden in ("switch", "number", "select", "button"):
        assert forbidden not in domains


async def test_source_without_measurement_is_rejected(hass: HomeAssistant) -> None:
    """Tanpa sensor kWh maupun daya, form menolak dengan pesan yang jelas."""
    apply_states(hass, MCB_RUMAH)

    result = await _walk_to_map_entities(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Kosong", CONF_VOLTAGE_ENTITY_ID: "sensor.mcb_rumah_phase_a_voltage"},
    )

    assert result["step_id"] == "map_entities"
    assert result["errors"] == {"base": "no_measurement_entity"}


async def test_unavailable_source_can_still_be_saved(hass: HomeAssistant) -> None:
    """Sumber yang sedang offline boleh disimpan - peringatan, bukan blokir."""
    hass.states.async_set(
        "sensor.ju_wei_dian_neng_biao_cw24_cw20_power",
        "unavailable",
        {"unit_of_measurement": "W", "device_class": "power"},
    )

    result = await _walk_to_map_entities(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Juwei",
            CONF_POWER_ENTITY_ID: "sensor.ju_wei_dian_neng_biao_cw24_cw20_power",
        },
    )

    assert result["step_id"] == "review"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_add_second_source_via_subentry(hass: HomeAssistant) -> None:
    """MCB TOKO ditambahkan sebagai Energy Source kedua lewat subentry flow."""
    apply_states(hass, MCB_RUMAH, MCB_TOKO)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        subentries_data=[
            {
                "data": MCB_RUMAH_MAPPING,
                "subentry_type": SUBENTRY_TYPE_ENERGY_SOURCE,
                "title": "MCB RUMAH",
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_ENERGY_SOURCE),
        context={"source": SOURCE_USER},
    )
    assert result["step_id"] == "pick_device"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"show_all_sensors": False}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], dict(MCB_TOKO_MAPPING)
    )
    assert result["step_id"] == "review"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    titles = {subentry.title for subentry in entry.subentries.values()}
    assert titles == {"MCB RUMAH", "MCB TOKO"}


async def test_duplicate_source_name_is_rejected(hass: HomeAssistant) -> None:
    """Dua sumber dengan nama sama ditolak supaya tidak tertukar di dashboard."""
    apply_states(hass, MCB_RUMAH, MCB_TOKO)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        subentries_data=[
            {
                "data": MCB_RUMAH_MAPPING,
                "subentry_type": SUBENTRY_TYPE_ENERGY_SOURCE,
                "title": "MCB RUMAH",
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_ENERGY_SOURCE),
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"show_all_sensors": False}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], dict(MCB_RUMAH_MAPPING) | {"name": "MCB RUMAH"}
    )

    assert result["step_id"] == "map_entities"
    assert result["errors"] == {"base": "name_duplicate"}


async def test_reconfigure_existing_source(hass: HomeAssistant) -> None:
    """Edit sumber: langsung ke pemetaan, lalu review, tanpa membuat entry baru."""
    apply_states(hass, MCB_RUMAH, MCB_TOKO)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        subentries_data=[
            {
                "data": MCB_RUMAH_MAPPING,
                "subentry_type": SUBENTRY_TYPE_ENERGY_SOURCE,
                "title": "MCB RUMAH",
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    subentry_id = next(iter(entry.subentries))
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_ENERGY_SOURCE),
        context={"source": "reconfigure", "subentry_id": subentry_id},
    )
    assert result["step_id"] == "map_entities"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        dict(MCB_RUMAH_MAPPING) | {"name": "MCB RUMAH LANTAI 1"},
    )
    assert result["step_id"] == "review"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    assert len(entry.subentries) == 1
    assert entry.subentries[subentry_id].title == "MCB RUMAH LANTAI 1"
