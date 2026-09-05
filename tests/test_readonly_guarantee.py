"""Jaminan bahwa integrasi ini tidak pernah bisa mengendalikan listrik.

Ini aturan non-negotiable dari spec (Executive Summary + O.4). Test di sini
sengaja dibuat kasar dan menyeluruh supaya siapa pun yang nanti menambah kode
baru akan langsung tersandung kalau melanggarnya - termasuk saya sendiri di
milestone berikutnya.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pln_prepaid_monitor.const import (
    CONF_ENERGY_ENTITY_ID,
    CONF_POWER_ENTITY_ID,
    DOMAIN,
    FORBIDDEN_PLATFORMS,
    PLATFORMS,
    SUBENTRY_TYPE_ENERGY_SOURCE,
)

from .conftest import apply_states, MCB_RUMAH, RELAY_ENTITIES

INTEGRATION_DIR = (
    Path(__file__).parent.parent / "custom_components" / "pln_prepaid_monitor"
)

def test_platform_list_is_locked() -> None:
    """Daftar platform dikunci, jadi penambahan berikutnya harus disengaja.

    ``number`` dan ``button`` masuk sejak D-039 supaya pencatatan token bisa
    dilakukan dari dashboard. Keduanya hanya menyentuh catatan token dan
    konfigurasi integrasi ini sendiri - dibuktikan oleh
    ``test_pressing_every_button_leaves_relays_untouched`` di bawah, yang
    menekan seluruh tombol dan mengubah seluruh isian lalu memastikan tidak
    ada entity relay yang bergerak.
    """
    assert set(PLATFORMS) == {
        Platform.SENSOR,
        Platform.BINARY_SENSOR,
        Platform.NUMBER,
        Platform.BUTTON,
        Platform.SELECT,
        Platform.TEXT,
    }


def test_forbidden_platforms_are_never_used() -> None:
    """Platform yang mengirim perintah ke perangkat tetap terlarang selamanya."""
    assert not FORBIDDEN_PLATFORMS & set(PLATFORMS)
    assert Platform.SWITCH in FORBIDDEN_PLATFORMS


def test_no_forbidden_platform_module_exists() -> None:
    """Tidak boleh ada file platform terlarang di dalam paket integrasi."""
    for platform in FORBIDDEN_PLATFORMS:
        assert not (INTEGRATION_DIR / f"{platform.value}.py").exists()


def test_no_control_verbs_anywhere_in_the_source() -> None:
    """Kata kerja pengendali perangkat tidak boleh muncul di kode mana pun."""
    forbidden = ("turn_on", "turn_off", "toggle", "async_call_service")
    offenders: list[str] = []
    for path in INTEGRATION_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        offenders.extend(
            f"{path.name}: {needle}" for needle in forbidden if needle in text
        )
    assert offenders == []


def test_only_the_notifier_may_call_services() -> None:
    """Pemanggilan service hanya boleh ada di satu file, di balik pagar pengaman.

    Sejak Milestone 6 integrasi ini perlu mengirim notifikasi, jadi larangan
    total memanggil service tidak lagi bisa dipertahankan. Yang dipertahankan
    adalah maksud aslinya: **tidak boleh ada jalan untuk mengendalikan
    listrik**. Karena itu seluruh pemanggilan service dipusatkan di satu file
    yang memeriksa domainnya lebih dulu, dan test ini menjaga agar tidak ada
    file kedua yang diam-diam ikut memanggil service.
    """
    callers = {
        path.name
        for path in INTEGRATION_DIR.rglob("*.py")
        if "services.async_call" in path.read_text(encoding="utf-8")
    }
    assert callers == {"notifier.py"}


def test_allowed_service_domains_are_notify_only() -> None:
    """Daftar domain yang boleh dipanggil dikunci ke notify saja."""
    from custom_components.pln_prepaid_monitor.notifier import (
        ALLOWED_SERVICE_DOMAINS,
    )

    assert set(ALLOWED_SERVICE_DOMAINS) == {"notify"}


@pytest.mark.parametrize(
    "target",
    [
        "switch.turn_off",
        "switch.mcb_rumah_switch",
        "homeassistant.turn_off",
        "script.matikan_listrik",
    ],
)
async def test_forbidden_service_target_is_refused(
    hass: HomeAssistant, target: str
) -> None:
    """Target di luar domain notify ditolak sebelum sempat dipanggil."""
    from custom_components.pln_prepaid_monitor.notifier import (
        ForbiddenServiceError,
        async_call_notify_target,
    )

    called: list[tuple[str, str]] = []

    async def _record(call) -> None:
        called.append((call.domain, call.service))

    hass.services.async_register("switch", "turn_off", _record)
    hass.services.async_register("homeassistant", "turn_off", _record)
    hass.services.async_register("script", "matikan_listrik", _record)

    with pytest.raises(ForbiddenServiceError):
        await async_call_notify_target(hass, target, "pesan", "judul")

    await hass.async_block_till_done()
    assert called == []


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
        # Menerima atau menolak usulan harga per kWh; hanya menyentuh
        # konfigurasi tarif milik integrasi ini sendiri.
        "resolve_rate_change",
        # Menyimpan isian jumlah kWh dan nominal sekarang sebagai template,
        # serta mengubah dan menghapusnya. Semuanya hanya menyentuh daftar
        # template milik integrasi ini sendiri.
        "save_topup_template",
        "update_topup_template",
        "delete_topup_template",
        # Mengirim satu pesan percobaan lewat notifier - satu-satunya jalur
        # yang boleh memanggil service, dan hanya ke domain notify.
        "test_notification",
        # Hanya membaca dan mengembalikan teks YAML; tidak mengubah apa pun.
        "generate_dashboard",
        # Menghapus riwayat milik integrasi ini saja, tidak pernah menyentuh
        # perangkat - dijaga terpisah di tests/test_retention.py.
        "purge_old_data",
    }
    for name in registered:
        assert not any(
            word in name for word in ("turn", "switch", "toggle", "power", "breaker")
        )


async def test_pressing_every_button_leaves_relays_untouched(
    hass: HomeAssistant,
) -> None:
    """Bukti bahwa number dan button di sini tidak menyentuh perangkat apa pun.

    Ini pengganti larangan platform yang dulu: alih-alih melarang berdasarkan
    nama platform, sekarang dibuktikan berdasarkan perilaku. Seluruh isian
    diubah dan seluruh tombol ditekan; tidak satu pun entity relay/breaker
    milik user boleh bergerak.
    """
    from custom_components.pln_prepaid_monitor.const import (
        CONF_CYCLE_PERIODS,
        CONF_SOURCE_IDS,
        CONF_TOKEN_ENABLED,
        SUBENTRY_TYPE_BILLING_GROUP,
    )

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
                "subentry_id": "src_rumah",
                "subentry_type": SUBENTRY_TYPE_ENERGY_SOURCE,
                "title": "MCB RUMAH",
                "unique_id": None,
            },
            {
                "data": {
                    "name": "PLN RUMAH",
                    CONF_SOURCE_IDS: ["src_rumah"],
                    CONF_CYCLE_PERIODS: ["day"],
                    CONF_TOKEN_ENABLED: True,
                },
                "subentry_id": "grp_rumah",
                "subentry_type": SUBENTRY_TYPE_BILLING_GROUP,
                "title": "PLN RUMAH",
                "unique_id": None,
            },
        ],
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    numbers = [
        state.entity_id
        for state in hass.states.async_all("number")
        if state.entity_id.startswith("number.pln_")
    ]
    buttons = [state.entity_id for state in hass.states.async_all("button")]
    selects = [state.entity_id for state in hass.states.async_all("select")]
    texts = [state.entity_id for state in hass.states.async_all("text")]
    assert numbers and buttons, "platform baru harus benar-benar membuat entity"
    assert selects and texts, "select dan text juga harus benar-benar dibuat"

    # Sebagian nilai memang akan ditolak - misalnya menyamakan ketiga ambang
    # jadi 1.0 melanggar urutan wajibnya. Yang diuji di sini bukan apakah
    # nilainya diterima, melainkan bahwa tidak ada relay yang bergerak entah
    # diterima maupun ditolak.
    from homeassistant.exceptions import HomeAssistantError

    for entity_id in numbers:
        try:
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": entity_id, "value": 1.0},
                blocking=True,
            )
        except HomeAssistantError:
            pass
        await hass.async_block_till_done()

    for entity_id in texts:
        try:
            await hass.services.async_call(
                "text",
                "set_value",
                {"entity_id": entity_id, "value": "uji"},
                blocking=True,
            )
        except HomeAssistantError:
            pass
        await hass.async_block_till_done()

    for entity_id in selects:
        state = hass.states.get(entity_id)
        for option in state.attributes.get("options", []):
            try:
                await hass.services.async_call(
                    "select",
                    "select_option",
                    {"entity_id": entity_id, "option": option},
                    blocking=True,
                )
            except HomeAssistantError:
                pass
            await hass.async_block_till_done()

    for entity_id in buttons:
        try:
            await hass.services.async_call(
                "button", "press", {"entity_id": entity_id}, blocking=True
            )
        except HomeAssistantError:
            pass
        await hass.async_block_till_done()

    after = {
        entity_id: hass.states.get(entity_id).state for entity_id in RELAY_ENTITIES
    }
    assert after == before
