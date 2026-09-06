"""`docs/dashboard-example.yaml` dikunci ke keluaran kodenya sendiri.

Berkas itu dulu dibangkitkan sekali lalu di-commit, dan skenario pembuatnya
tidak tersimpan di mana pun. Akibatnya tidak ada yang bisa membangkitkannya
ulang, dan tidak ada pula yang tahu kalau ia sudah menyimpang dari kode -
persis kelas kesalahan yang jadi pokok D-053: salah, tapi tidak terlihat salah.

Sekarang skenarionya ada di sini, dan test ini gagal begitu berkasnya tidak
lagi sama dengan yang dihasilkan kode.

Cara memperbaruinya sesudah mengubah dashboard - **jangan** diedit tangan:

    PLN_WRITE_EXAMPLE=1 .venv/bin/python -m pytest tests/test_dashboard_example.py

Skenarionya sengaja dipilih supaya contohnya berguna, bukan minimal: dua
kelompok tagihan, keduanya mencatat token, salah satunya punya template
pengisian dan yang lain belum - jadi pembaca melihat kedua keadaan itu.
"""

from __future__ import annotations

import os
import pathlib
import re

import pytest
import yaml
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pln_prepaid_monitor.const import (
    CONF_CURRENT_ENTITY_ID,
    CONF_CYCLE_PERIODS,
    CONF_ENERGY_ENTITY_ID,
    CONF_FIXED_CHARGE_PERIOD,
    CONF_FIXED_CHARGE_RP,
    CONF_FREQUENCY_ENTITY_ID,
    CONF_POWER_ENTITY_ID,
    CONF_RATE_HISTORY,
    CONF_RATE_RP_PER_KWH,
    CONF_ROUNDING_MODE,
    CONF_ROUNDING_UNIT_RP,
    CONF_SOURCE_IDS,
    CONF_TARIFF_ID,
    CONF_TOKEN_ENABLED,
    CONF_TOKEN_PRESETS,
    CONF_VOLTAGE_ENTITY_ID,
    DOMAIN,
    SUBENTRY_TYPE_BILLING_GROUP,
    SUBENTRY_TYPE_ENERGY_SOURCE,
    SUBENTRY_TYPE_TARIFF,
)
from custom_components.pln_prepaid_monitor.dashboard import build_dashboard

from .conftest import MCB_RUMAH, MCB_TOKO, apply_states

EXAMPLE = pathlib.Path("docs/dashboard-example.yaml")

# device_id sungguhan berbeda di tiap instalasi dan diacak ulang tiap test
# jalan, jadi tidak boleh masuk ke berkas contoh.
DEVICE_PLACEHOLDER = "ganti-dengan-id-perangkat-kelompok-tagihan-anda"
RE_DEVICE_ID = re.compile(r"^[0-9a-f]{32}$")

HEADER = """\
# Contoh dashboard PLN Prepaid Monitor
#
# JANGAN disalin mentah-mentah. entity_id di bawah ini mengikuti nama kelompok
# tagihan "PLN RUMAH" dan "PLN TOKO"; punya Anda kemungkinan berbeda.
#
# Cara mendapatkan versi yang sudah benar untuk instalasi Anda sendiri:
#   Developer Tools -> Actions -> "Buatkan dashboard" -> Perform action
# Hasilnya berupa YAML yang entity_id-nya sudah diisi otomatis.
#
# Cara memakainya:
#   Settings -> Dashboards -> + Add dashboard -> New dashboard from scratch
#   -> buka dashboard barunya -> pensil (kanan atas) -> titik tiga
#   -> Raw configuration editor -> tempelkan seluruh isi di bawah ini.
#
# Semua kartu di sini bawaan Home Assistant. Tidak perlu HACS.
#
# Berkas ini dibangkitkan oleh tests/test_dashboard_example.py - jangan diedit
# dengan tangan. Cara memperbaruinya ada di docstring test itu.

"""

# Nilai dari struk PLN nyata, sama seperti yang dipakai test lain.
# Diberi nama supaya contohnya juga memperlihatkan template bernama, bukan
# cuma yang berlabel angka.
PRESETS = [
    {"kwh": 825.0, "nominal_rp": 1_002_500, "name": "Beli besar"},
    {"kwh": 425.0, "nominal_rp": 503_000, "name": "Beli kecil"},
]


def _source(key: str, name: str) -> dict:
    """Satu sumber energi, memakai entity nyata dari conftest."""
    return {
        "data": {
            "name": name,
            CONF_ENERGY_ENTITY_ID: f"sensor.{key}_total_energy",
            CONF_POWER_ENTITY_ID: f"sensor.{key}_phase_a_power",
            CONF_VOLTAGE_ENTITY_ID: f"sensor.{key}_phase_a_voltage",
            CONF_CURRENT_ENTITY_ID: f"sensor.{key}_phase_a_current",
            CONF_FREQUENCY_ENTITY_ID: f"sensor.{key}_supply_frequency",
        },
        "subentry_id": f"src_{key}",
        "subentry_type": SUBENTRY_TYPE_ENERGY_SOURCE,
        "title": name,
        "unique_id": None,
    }


TARIFF = {
    "data": {
        "name": "Tarif R-1",
        CONF_RATE_RP_PER_KWH: 1444.70,
        CONF_FIXED_CHARGE_RP: 0.0,
        CONF_FIXED_CHARGE_PERIOD: "monthly",
        CONF_ROUNDING_MODE: "nearest",
        CONF_ROUNDING_UNIT_RP: 1.0,
        CONF_RATE_HISTORY: [],
    },
    "subentry_id": "tar_r1",
    "subentry_type": SUBENTRY_TYPE_TARIFF,
    "title": "Tarif R-1",
    "unique_id": None,
}


def _group(key: str, name: str, presets: list[dict]) -> dict:
    """Satu kelompok tagihan, dengan tarif dan pencatatan token menyala."""
    return {
        "data": {
            "name": name,
            CONF_SOURCE_IDS: [f"src_{key}"],
            CONF_CYCLE_PERIODS: ["day", "month"],
            CONF_TARIFF_ID: "tar_r1",
            CONF_TOKEN_ENABLED: True,
            CONF_TOKEN_PRESETS: presets,
        },
        "subentry_id": f"grp_{key}",
        "subentry_type": SUBENTRY_TYPE_BILLING_GROUP,
        "title": name,
        "unique_id": None,
    }


def _mask_device_ids(value):
    """Ganti device_id acak dengan penanda yang bisa dibaca manusia."""
    if isinstance(value, str):
        return DEVICE_PLACEHOLDER if RE_DEVICE_ID.match(value) else value
    if isinstance(value, dict):
        return {key: _mask_device_ids(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_device_ids(item) for item in value]
    return value


async def _render(hass: HomeAssistant) -> str:
    """Bangkitkan contohnya, persis lewat kode yang dipakai layanannya."""
    apply_states(hass, MCB_RUMAH, MCB_TOKO)
    await hass.config.async_set_time_zone("Asia/Jakarta")
    # Contohnya berbahasa Indonesia, jadi entity_id-nya pun berbahasa Indonesia.
    await hass.config.async_update(currency="IDR", language="id")

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        subentries_data=[
            _source("mcb_rumah", "MCB RUMAH"),
            _source("mcb_toko", "MCB TOKO"),
            TARIFF,
            _group("mcb_rumah", "PLN RUMAH", PRESETS),
            # Kelompok kedua sengaja tanpa template, supaya contohnya juga
            # menunjukkan tampilan sebelum ada template sama sekali.
            _group("mcb_toko", "PLN TOKO", []),
        ],
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    config = _mask_device_ids(build_dashboard(hass, entry.runtime_data))
    body = yaml.dump(config, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return HEADER + body


async def test_committed_example_matches_the_code(hass: HomeAssistant) -> None:
    """Contoh yang ter-commit harus sama dengan yang dihasilkan kode.

    Kalau test ini merah sesudah Anda mengubah dashboard, itu bukan kegagalan -
    itu pengingat untuk membangkitkan ulang berkasnya, bukan menambalnya.
    """
    rendered = await _render(hass)

    if os.environ.get("PLN_WRITE_EXAMPLE"):
        EXAMPLE.write_text(rendered, encoding="utf-8")
        pytest.skip("docs/dashboard-example.yaml ditulis ulang")

    assert rendered == EXAMPLE.read_text(encoding="utf-8"), (
        "docs/dashboard-example.yaml tidak lagi sama dengan keluaran kode. "
        "Bangkitkan ulang: PLN_WRITE_EXAMPLE=1 pytest tests/test_dashboard_example.py"
    )


async def test_the_example_needs_no_hacs_cards(hass: HomeAssistant) -> None:
    """Contoh bawaan harus bisa ditempel tanpa memasang apa pun dari HACS."""
    config = yaml.safe_load(await _render(hass))

    types: set[str] = set()

    def walk(cards: list[dict]) -> None:
        for card in cards:
            types.add(card.get("type", ""))
            walk(card.get("cards", []))
            if nested := card.get("card"):
                walk([nested])

    for view in config["views"]:
        for section in view.get("sections", []):
            walk(section.get("cards", []))
        walk(view.get("cards", []))

    assert not {name for name in types if name.startswith("custom:")}
