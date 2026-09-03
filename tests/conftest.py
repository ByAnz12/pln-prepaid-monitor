"""Fixture bersama untuk seluruh test.

Data entity yang dipakai di test ini bukan karangan: semuanya diambil dari
inventaris entity nyata milik user (spec Bagian O), termasuk dua kejanggalan
yang benar-benar ada di instalasi itu - MCB RUMAH yang memakai satuan kW untuk
daya, dan battery1 yang punya device_class energy tanpa state_class.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(request: pytest.FixtureRequest) -> Generator[None]:
    """Izinkan Home Assistant memuat custom_components dari repo ini.

    Urutannya penting: ``recorder_mock`` harus disiapkan sebelum ``hass``, karena
    database recorder dibuat sebelum instance Home Assistant berjalan. Kalau
    fixture ini langsung meminta ``enable_custom_integrations`` (yang menarik
    ``hass``), test yang memakai recorder akan gagal sebelum sempat jalan.
    """
    if "recorder_mock" in request.fixturenames:
        request.getfixturevalue("recorder_mock")
    request.getfixturevalue("enable_custom_integrations")
    yield


# --- entity nyata dari instance user (spec O.2) ------------------------------

MCB_RUMAH = {
    "sensor.mcb_rumah_total_energy": (
        "15498.27",
        {
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "total_increasing",
            "friendly_name": "MCB RUMAH Total energy",
        },
    ),
    # Perhatikan satuannya kW, bukan W - ini nyata, bukan dibuat-buat.
    "sensor.mcb_rumah_phase_a_power": (
        "1.234",
        {
            "unit_of_measurement": "kW",
            "device_class": "power",
            "state_class": "measurement",
            "friendly_name": "MCB RUMAH Phase A power",
        },
    ),
    "sensor.mcb_rumah_phase_a_voltage": (
        "221.4",
        {
            "unit_of_measurement": "V",
            "device_class": "voltage",
            "state_class": "measurement",
        },
    ),
    "sensor.mcb_rumah_phase_a_current": (
        "5.6",
        {
            "unit_of_measurement": "A",
            "device_class": "current",
            "state_class": "measurement",
        },
    ),
    "sensor.mcb_rumah_supply_frequency": (
        "50.0",
        {
            "unit_of_measurement": "Hz",
            "device_class": "frequency",
            "state_class": "measurement",
        },
    ),
}

MCB_TOKO = {
    "sensor.mcb_toko_total_energy": (
        "15114.43",
        {
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "total_increasing",
        },
    ),
    "sensor.mcb_toko_phase_a_power": (
        "830",
        {
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
        },
    ),
    "sensor.mcb_toko_phase_a_voltage": (
        "220.1",
        {
            "unit_of_measurement": "V",
            "device_class": "voltage",
            "state_class": "measurement",
        },
    ),
    "sensor.mcb_toko_phase_a_current": (
        "3.8",
        {
            "unit_of_measurement": "A",
            "device_class": "current",
            "state_class": "measurement",
        },
    ),
    "sensor.mcb_toko_supply_frequency": (
        "49.98",
        {
            "unit_of_measurement": "Hz",
            "device_class": "frequency",
            "state_class": "measurement",
        },
    ),
}

# Edge case nyata: device_class energy TANPA state_class sama sekali (spec O.3).
BATTERY1 = {
    "sensor.battery1_total_energy_meter": (
        "812.5",
        {"unit_of_measurement": "kWh", "device_class": "energy"},
    ),
}

# Sumber yang memang sedang offline di instance user (spec O.3).
JUWEI = {
    "sensor.ju_wei_dian_neng_biao_cw24_cw20_power": (
        "unavailable",
        {"unit_of_measurement": "W", "device_class": "power"},
    ),
}

# Entity kontrol yang tidak boleh pernah tersentuh sistem ini (spec O.4).
RELAY_ENTITIES = {
    "switch.mcb_rumah_switch": ("on", {"friendly_name": "MCB RUMAH switch"}),
    "switch.mcb_toko_switch": ("on", {}),
    "switch.0x385b44fffed7fa8d": ("on", {}),
    "switch.0x385b44fffed7fa8d_power_breaker": ("on", {}),
    "switch.mcb_tongou_child_lock": ("off", {}),
    "number.0x385b44fffed7fa8d_over_current_threshold": ("40", {}),
}


def apply_states(hass: Any, *groups: dict[str, tuple[str, dict[str, Any]]]) -> None:
    """Pasang state entity sumber ke instance Home Assistant test."""
    for group in groups:
        for entity_id, (state, attributes) in group.items():
            hass.states.async_set(entity_id, state, attributes)


@pytest.fixture
def real_states(hass: Any) -> None:
    """Semua entity nyata yang relevan, sekaligus."""
    apply_states(hass, MCB_RUMAH, MCB_TOKO, BATTERY1, JUWEI, RELAY_ENTITIES)
