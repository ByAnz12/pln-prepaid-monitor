"""Ikon integrasi: aset brand dan ``icons.json``.

Yang dijaga di sini bukan selera, melainkan hal-hal yang diam-diam rusak:
nama kunci yang berubah tanpa ikonnya ikut berubah, dan aset brand yang
salah tempat sehingga Home Assistant tidak pernah menemukannya.
"""

from __future__ import annotations

import json
import pathlib
import struct

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers.icon import async_get_icons

from custom_components.pln_prepaid_monitor.const import DOMAIN

COMPONENT = pathlib.Path("custom_components/pln_prepaid_monitor")
ICONS = json.loads((COMPONENT / "icons.json").read_text(encoding="utf-8"))
STRINGS = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))

# device_class sudah memberi ikon yang tepat untuk entity ini. Menimpanya hanya
# membuat integrasi ini terlihat beda sendiri dari seluruh Home Assistant.
LEFT_TO_DEVICE_CLASS = {
    ("sensor", "power"),
    ("sensor", "voltage"),
    ("sensor", "current"),
    ("sensor", "frequency"),
    ("binary_sensor", "available"),
    ("binary_sensor", "ledger_hold"),
}


def _png_size(path: pathlib.Path) -> tuple[int, int]:
    """Baca lebar dan tinggi dari header PNG, tanpa perlu pustaka gambar."""
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} bukan PNG"
    return struct.unpack(">II", header[16:24])


# --- aset brand -------------------------------------------------------------


def test_brand_folder_is_where_home_assistant_looks() -> None:
    """Home Assistant hanya melihat folder bernama persis ``brand``.

    ``Integration.has_branding`` memeriksa keberadaan entri bernama ``brand``
    di tingkat atas folder integrasi. Salah nama sedikit saja - ``brands``,
    ``branding`` - dan ikonnya tidak pernah muncul, tanpa pesan error apa pun.
    """
    assert (COMPONENT / "brand").is_dir()


@pytest.mark.parametrize(("name", "expected"), [("icon.png", 256), ("icon@2x.png", 512)])
def test_brand_icons_are_square_and_the_right_size(name: str, expected: int) -> None:
    """Ukuran mengikuti ketentuan repositori brands Home Assistant."""
    width, height = _png_size(COMPONENT / "brand" / name)
    assert width == height == expected


def test_brand_icons_stay_small_enough_to_ship() -> None:
    """Aset ikut terunduh setiap kali integrasi dipasang; jangan sampai gemuk."""
    for path in (COMPONENT / "brand").iterdir():
        assert path.stat().st_size < 200_000, path.name


# --- icons.json -------------------------------------------------------------


async def test_home_assistant_can_actually_load_our_icons(hass: HomeAssistant) -> None:
    """Uji lewat pemuat asli, bukan sekadar membaca berkasnya sendiri.

    Ini yang membuktikan berkasnya ada di tempat yang benar dan berformat yang
    dimengerti Home Assistant - bukan hanya JSON yang kebetulan valid.
    """
    icons = await async_get_icons(hass, "entity", integrations=[DOMAIN])

    assert icons[DOMAIN]["sensor"]["token_status"]["state"]["hold"] == "mdi:pause-circle"

    services = await async_get_icons(hass, "services", integrations=[DOMAIN])
    assert services[DOMAIN]["purge_old_data"] == {"service": "mdi:database-remove"}


def test_every_icon_points_at_an_entity_that_exists() -> None:
    """Ikon untuk kunci yang tidak ada hanya menumpuk diam-diam saat refactor."""
    for platform, keys in ICONS["entity"].items():
        known = set(STRINGS["entity"][platform])
        assert set(keys) <= known, f"{platform}: {set(keys) - known}"


def test_every_service_has_an_icon() -> None:
    """Semua layanan tampil berdampingan di Developer Tools - jangan ada yang polos."""
    assert set(ICONS["services"]) == set(STRINGS["services"])


def test_token_status_has_an_icon_for_every_state() -> None:
    """Status token adalah inti dashboard; satu state tanpa ikon langsung terlihat."""
    states = set(STRINGS["entity"]["sensor"]["token_status"]["state"])
    assert set(ICONS["entity"]["sensor"]["token_status"]["state"]) == states


def test_we_do_not_override_icons_device_class_already_gets_right() -> None:
    """Keputusan yang sengaja diambil, jadi dikunci di sini.

    Tegangan, arus, frekuensi, daya, dan status koneksi sudah mendapat ikon
    yang tepat dari ``device_class``-nya. Menimpanya tidak menambah informasi
    apa pun, hanya membuat integrasi ini tampil beda sendiri.
    """
    for platform, key in LEFT_TO_DEVICE_CLASS:
        assert key not in ICONS["entity"].get(platform, {}), f"{platform}.{key}"
