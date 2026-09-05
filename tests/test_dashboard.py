"""Test pembuat dashboard: entity-nya nyata, kartunya bawaan Home Assistant."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pln_prepaid_monitor.const import (
    CONF_CYCLE_PERIODS,
    CONF_ENERGY_ENTITY_ID,
    CONF_FIXED_CHARGE_PERIOD,
    CONF_FIXED_CHARGE_RP,
    CONF_POWER_ENTITY_ID,
    CONF_RATE_HISTORY,
    CONF_RATE_RP_PER_KWH,
    CONF_ROUNDING_MODE,
    CONF_ROUNDING_UNIT_RP,
    CONF_SOURCE_IDS,
    CONF_TARIFF_ID,
    CONF_TOKEN_ENABLED,
    CONF_VOLTAGE_ENTITY_ID,
    DOMAIN,
    SERVICE_GENERATE_DASHBOARD,
    SUBENTRY_TYPE_BILLING_GROUP,
    SUBENTRY_TYPE_ENERGY_SOURCE,
    SUBENTRY_TYPE_TARIFF,
)

from custom_components.pln_prepaid_monitor.dashboard import view_cards

from .conftest import apply_states, MCB_RUMAH

RUMAH_ID = "src_rumah"
TARIFF_ID = "tar_r1"
GROUP_ID = "grp_rumah"

SOURCE_SUBENTRY = {
    "data": {
        "name": "MCB RUMAH",
        CONF_ENERGY_ENTITY_ID: "sensor.mcb_rumah_total_energy",
        CONF_POWER_ENTITY_ID: "sensor.mcb_rumah_phase_a_power",
        CONF_VOLTAGE_ENTITY_ID: "sensor.mcb_rumah_phase_a_voltage",
    },
    "subentry_id": RUMAH_ID,
    "subentry_type": SUBENTRY_TYPE_ENERGY_SOURCE,
    "title": "MCB RUMAH",
    "unique_id": None,
}

TARIFF_SUBENTRY = {
    "data": {
        "name": "Tarif R-1",
        CONF_RATE_RP_PER_KWH: 1444.70,
        CONF_FIXED_CHARGE_RP: 0.0,
        CONF_FIXED_CHARGE_PERIOD: "monthly",
        CONF_ROUNDING_MODE: "nearest",
        CONF_ROUNDING_UNIT_RP: 1.0,
        CONF_RATE_HISTORY: [],
    },
    "subentry_id": TARIFF_ID,
    "subentry_type": SUBENTRY_TYPE_TARIFF,
    "title": "Tarif R-1",
    "unique_id": None,
}


def _group(**overrides) -> dict:
    """Kelompok tagihan lengkap dengan tarif dan token."""
    data = {
        "name": "PLN RUMAH",
        CONF_SOURCE_IDS: [RUMAH_ID],
        CONF_CYCLE_PERIODS: ["day", "month"],
        CONF_TARIFF_ID: TARIFF_ID,
        CONF_TOKEN_ENABLED: True,
    }
    data.update(overrides)
    return {
        "data": data,
        "subentry_id": GROUP_ID,
        "subentry_type": SUBENTRY_TYPE_BILLING_GROUP,
        "title": "PLN RUMAH",
        "unique_id": None,
    }


async def _setup(hass: HomeAssistant, *subentries) -> MockConfigEntry:
    """Pasang integrasi lengkap."""
    await hass.config.async_set_time_zone("Asia/Jakarta")
    await hass.config.async_update(currency="IDR")
    entry = MockConfigEntry(domain=DOMAIN, data={}, subentries_data=list(subentries))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _generate(hass: HomeAssistant) -> dict:
    """Panggil layanan pembuat dashboard dan ambil hasilnya."""
    return await hass.services.async_call(
        DOMAIN, SERVICE_GENERATE_DASHBOARD, {}, blocking=True, return_response=True
    )


def _all_cards(config: dict) -> list[dict]:
    """Ratakan seluruh kartu, termasuk yang bersarang di dalam stack."""
    cards: list[dict] = []

    def _walk(items: list[dict]) -> None:
        for card in items:
            cards.append(card)
            _walk(card.get("cards", []))
            if nested := card.get("card"):
                cards.append(nested)
                _walk(nested.get("cards", []))

    for view in config["views"]:
        _walk(view_cards(view))
    return cards


def _all_entity_ids(config: dict) -> set[str]:
    """Semua entity_id yang dirujuk dashboard."""
    found: set[str] = set()
    for card in _all_cards(config):
        for entity in card.get("entities", []):
            found.add(entity if isinstance(entity, str) else entity["entity"])
        if entity := card.get("entity"):
            found.add(entity)
        for condition in card.get("conditions", []):
            if entity := condition.get("entity"):
                found.add(entity)
    return found


async def test_dashboard_is_built_for_each_group(hass: HomeAssistant) -> None:
    """Satu halaman per kelompok tagihan, dengan judul dan path yang benar."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    from custom_components.pln_prepaid_monitor.dashboard import build_dashboard

    config = build_dashboard(hass, entry.runtime_data)

    assert len(config["views"]) == 1
    assert config["views"][0]["title"] == "PLN RUMAH"
    assert config["views"][0]["path"] == "pln-rumah"


async def test_every_referenced_entity_actually_exists(
    hass: HomeAssistant,
) -> None:
    """Dashboard tidak boleh merujuk entity yang tidak ada.

    Inilah alasan dashboard dibuatkan, bukan disalin dari contoh statis: satu
    entity_id yang salah ketik menghasilkan kartu kosong tanpa penjelasan.
    """
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    from custom_components.pln_prepaid_monitor.dashboard import build_dashboard

    config = build_dashboard(hass, entry.runtime_data)
    referenced = _all_entity_ids(config)

    assert referenced
    for entity_id in referenced:
        assert hass.states.get(entity_id) is not None, entity_id


async def test_only_built_in_cards_are_used(hass: HomeAssistant) -> None:
    """Dashboard harus berfungsi penuh tanpa HACS atau kartu pihak ketiga."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    from custom_components.pln_prepaid_monitor.dashboard import build_dashboard

    built_in = {
        "entities",
        "gauge",
        "glance",
        "grid",
        # Kartu judul bawaan Home Assistant sejak 2024.9, dipakai sebagai
        # judul tiap bagian pada tata letak sections.
        "heading",
        "markdown",
        "button",
        "conditional",
        "vertical-stack",
        "horizontal-stack",
        "statistics-graph",
        "history-graph",
    }
    used = {card["type"] for card in _all_cards(build_dashboard(hass, entry.runtime_data))}

    assert used <= built_in, used - built_in
    # Tidak ada kartu Mushroom atau custom lainnya.
    assert not any(card_type.startswith("custom:") for card_type in used)


async def test_all_sections_from_the_spec_are_present(
    hass: HomeAssistant,
) -> None:
    """Empat seksi spec J ada: status, sekarang, biaya, token, plus riwayat."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    from custom_components.pln_prepaid_monitor.dashboard import build_dashboard

    cards = _all_cards(build_dashboard(hass, entry.runtime_data))
    # Kartu markdown menaruh judulnya di dalam isi, bukan di kunci "title".
    titles = {card.get("title", "") for card in cards} | {
        card.get("content", "") for card in cards
    }

    assert any("Status" in title for title in titles)
    assert any("Cost" in title for title in titles)
    assert any("Token" in title for title in titles)
    # Riwayat memakai statistics-graph, yang membaca long-term statistics.
    assert any(card["type"] == "statistics-graph" for card in cards)


async def test_group_without_tariff_gets_no_cost_cards(
    hass: HomeAssistant,
) -> None:
    """Tanpa tarif, tidak ada kartu biaya yang menunjuk entity yang tidak ada."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(
        hass, SOURCE_SUBENTRY, _group(**{CONF_TARIFF_ID: None, CONF_TOKEN_ENABLED: False})
    )

    from custom_components.pln_prepaid_monitor.dashboard import build_dashboard

    config = build_dashboard(hass, entry.runtime_data)
    referenced = _all_entity_ids(config)

    assert not any("cost" in entity_id for entity_id in referenced)
    assert not any("token" in entity_id for entity_id in referenced)
    for entity_id in referenced:
        assert hass.states.get(entity_id) is not None, entity_id


async def test_every_button_asks_for_confirmation(hass: HomeAssistant) -> None:
    """Setiap tombol di dashboard mengubah catatan, jadi wajib dikonfirmasi.

    Ini invarian yang paling penting dijaga: tidak boleh ada tombol yang
    langsung bekerja hanya karena tersenggol.
    """
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    from custom_components.pln_prepaid_monitor.dashboard import build_dashboard

    buttons = [
        card
        for card in _all_cards(build_dashboard(hass, entry.runtime_data))
        if card["type"] == "button"
    ]

    assert buttons
    for button in buttons:
        action = button["tap_action"]
        assert "confirmation" in action, button["name"]
        assert action["target"]["device_id"], button["name"]
        # Hanya layanan milik integrasi ini yang boleh dipanggil dari dashboard.
        assert action["perform_action"].startswith(f"{DOMAIN}.")


async def test_hold_buttons_target_the_hold_action(hass: HomeAssistant) -> None:
    """Dua tombol keputusan penahanan memanggil layanan yang benar."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    from custom_components.pln_prepaid_monitor.dashboard import build_dashboard

    actions = [
        card["tap_action"]["data"]["action"]
        for card in _all_cards(build_dashboard(hass, entry.runtime_data))
        if card["type"] == "button"
        and card["tap_action"]["perform_action"].endswith("resolve_ledger_hold")
    ]

    assert sorted(actions) == ["accept", "ignore"]


async def test_maintenance_card_is_present_and_confirmed(
    hass: HomeAssistant,
) -> None:
    """Kartu perawatan ada, dan tombol hapusnya wajib dikonfirmasi (spec J)."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    from custom_components.pln_prepaid_monitor.dashboard import build_dashboard

    purge_buttons = [
        card
        for card in _all_cards(build_dashboard(hass, entry.runtime_data))
        if card["type"] == "button"
        and card["tap_action"]["perform_action"].endswith("purge_old_data")
    ]

    assert len(purge_buttons) == 1
    assert "confirmation" in purge_buttons[0]["tap_action"]


async def test_hold_cards_are_hidden_until_needed(hass: HomeAssistant) -> None:
    """Kartu penahanan dibungkus conditional, jadi tidak mengganggu saat normal."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    from custom_components.pln_prepaid_monitor.dashboard import build_dashboard

    conditionals = [
        card
        for card in _all_cards(build_dashboard(hass, entry.runtime_data))
        if card["type"] == "conditional"
        and "ledger_hold" in card["conditions"][0]["entity"]
    ]

    assert len(conditionals) == 1
    condition = conditionals[0]["conditions"][0]
    assert condition["state"] == "on"
    assert "ledger_hold" in condition["entity"]


async def test_service_returns_pasteable_yaml(hass: HomeAssistant) -> None:
    """Layanan mengembalikan konfigurasi siap tempel, tanpa mengubah dashboard."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    response = await _generate(hass)

    assert len(response["views"]) == 1
    assert response["views"][0]["title"] == "PLN RUMAH"

    # Developer Tools menampilkan response sebagai YAML; hasil tampilan itulah
    # yang disalin user, jadi ia harus bisa dibaca kembali sebagai YAML utuh.
    from homeassistant.util.yaml import dump, parse_yaml

    assert parse_yaml(dump(response)) == response


async def test_response_is_nothing_but_the_dashboard_config(
    hass: HomeAssistant,
) -> None:
    """Response tidak boleh punya kunci tambahan di luar skema Lovelace.

    Regresi: response pernah berbentuk ``{"yaml": ..., "views": 1}``. User yang
    menyalin seluruh response - hal paling wajar dilakukan - ditolak Raw
    configuration editor dengan "At path: views -- Expected an array value, but
    received: 1", karena ``views`` di sana berisi jumlah view, bukan daftarnya.
    """
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    response = await _generate(hass)

    # Persis kunci yang divalidasi frontend Lovelace, tidak satu pun tambahan.
    assert set(response) == {"views"}
    assert isinstance(response["views"], list)
    assert all(isinstance(view, dict) for view in response["views"])


async def test_service_refuses_when_there_is_nothing_to_build(
    hass: HomeAssistant,
) -> None:
    """Tanpa kelompok tagihan, layanan menolak dengan pesan yang jelas."""
    await _setup(hass, SOURCE_SUBENTRY)

    with pytest.raises(ServiceValidationError):
        await _generate(hass)


async def test_two_groups_get_two_pages(hass: HomeAssistant) -> None:
    """PLN RUMAH dan PLN TOKO masing-masing dapat halaman sendiri."""
    apply_states(hass, MCB_RUMAH)
    second = {
        "data": {
            "name": "PLN TOKO",
            CONF_SOURCE_IDS: [RUMAH_ID],
            CONF_CYCLE_PERIODS: ["day"],
            CONF_TOKEN_ENABLED: False,
        },
        "subentry_id": "grp_toko",
        "subentry_type": SUBENTRY_TYPE_BILLING_GROUP,
        "title": "PLN TOKO",
        "unique_id": None,
    }
    await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group(), second)

    response = await _generate(hass)

    paths = {view["path"] for view in response["views"]}
    assert paths == {"pln-rumah", "pln-toko"}


async def test_every_key_the_dashboard_asks_for_really_exists(
    hass: HomeAssistant,
) -> None:
    """Kunci yang tidak cocok membuat baris hilang dari dashboard tanpa error.

    Regresi: dashboard meminta ``average_daily_usage`` sementara sensornya
    dibuat dengan kunci ``avg_daily_usage``. ``_resolve`` melewati kunci yang
    tidak ketemu tanpa bersuara, jadi baris "Rata-rata pemakaian harian" hilang
    diam-diam dari kartu Token - baru ketahuan setelah user mengirim tangkapan
    layar dashboardnya.
    """
    from custom_components.pln_prepaid_monitor.dashboard import (
        GROUP_KEYS,
        SOURCE_KEYS,
        collect_views,
    )

    from custom_components.pln_prepaid_monitor.const import (
        CONF_CURRENT_ENTITY_ID,
        CONF_FREQUENCY_ENTITY_ID,
    )

    apply_states(hass, MCB_RUMAH)
    # Sumber dengan kelima kanal terpetakan, supaya setiap kunci yang diminta
    # dashboard memang seharusnya ada.
    full_source = {
        **SOURCE_SUBENTRY,
        "data": {
            **SOURCE_SUBENTRY["data"],
            CONF_CURRENT_ENTITY_ID: "sensor.mcb_rumah_phase_a_current",
            CONF_FREQUENCY_ENTITY_ID: "sensor.mcb_rumah_supply_frequency",
        },
    }
    entry = await _setup(hass, full_source, TARIFF_SUBENTRY, _group())

    view = collect_views(hass, entry.runtime_data)[0]

    # Konfigurasi ini lengkap - ada tarif, token, dan kelima kanal - jadi setiap
    # kunci yang diminta dashboard wajib ketemu entity-nya.
    assert not set(GROUP_KEYS) - set(view.entities)
    assert not set(SOURCE_KEYS) - set(view.sources[0].entities)


async def test_templates_survive_the_yaml_round_trip(hass: HomeAssistant) -> None:
    """Yang ditempel user adalah YAML-nya, bukan struktur Python-nya.

    Kartu markdown kita berisi template Jinja bertingkat baris. YAML gaya
    kutip-tunggal melipat baris panjang, dan pelipatan yang salah akan merusak
    tabelnya - kerusakan yang tidak akan terlihat di test mana pun yang hanya
    memeriksa dict hasil ``build_dashboard``.
    """
    from homeassistant.util.yaml import dump, parse_yaml

    from custom_components.pln_prepaid_monitor.dashboard import (
        build_dashboard,
        view_cards,
    )

    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    config = build_dashboard(hass, entry.runtime_data)
    restored = parse_yaml(dump(config))

    assert restored == config

    # Dan template yang sudah melewati YAML tetap bisa dirender.
    from homeassistant.helpers.template import Template

    markdowns = [
        card["content"]
        for view in restored["views"]
        for card in view_cards(view)
        if card.get("type") == "markdown"
    ]
    assert markdowns
    for content in markdowns:
        Template(content, hass).async_render(parse_result=False)


async def test_hacs_cards_never_appear_unless_asked_for(
    hass: HomeAssistant,
) -> None:
    """Bawaan harus tetap berfungsi penuh tanpa memasang apa pun lewat HACS."""
    from custom_components.pln_prepaid_monitor.dashboard import (
        LAYOUT_MASONRY,
        LAYOUT_SECTIONS,
        build_dashboard,
    )

    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    for layout in (LAYOUT_SECTIONS, LAYOUT_MASONRY):
        config = build_dashboard(hass, entry.runtime_data, layout)
        used = {card["type"] for card in _all_cards(config)}
        assert not any(name.startswith("custom:") for name in used), layout


async def test_hacs_layout_says_what_to_install(hass: HomeAssistant) -> None:
    """Kartu pihak ketiga yang belum dipasang tampil sebagai kotak merah.

    Karena itu dashboard varian ini harus menyebut sendiri apa yang perlu
    dipasang - kalau tidak, user hanya melihat kotak merah tanpa tahu sebabnya.
    """
    from custom_components.pln_prepaid_monitor.dashboard import (
        HACS_CARDS,
        LAYOUT_HACS,
        build_dashboard,
    )

    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    cards = _all_cards(build_dashboard(hass, entry.runtime_data, LAYOUT_HACS))
    custom = {card["type"] for card in cards if card["type"].startswith("custom:")}
    assert custom, "varian HACS harus benar-benar memakai kartu HACS"

    notes = " ".join(card.get("content", "") for card in cards)
    for name in HACS_CARDS:
        assert name in notes.lower(), name
    # Dan setiap kartu custom yang dipakai memang berasal dari daftar itu.
    for card_type in custom:
        assert any(name.split("-")[0] in card_type for name in HACS_CARDS), card_type


async def test_hacs_layout_keeps_the_number_boxes_built_in(
    hass: HomeAssistant,
) -> None:
    """Kartu number Mushroom memakai penggeser - menyulitkan mengetik kWh persis.

    Jadi isian angka sengaja tetap kartu bawaan, sekalipun varian HACS dipilih.
    Kecantikan tidak boleh dibayar dengan mempersulit tugas utamanya.
    """
    from custom_components.pln_prepaid_monitor.dashboard import (
        LAYOUT_HACS,
        build_dashboard,
        collect_views,
    )

    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    topup = collect_views(hass, entry.runtime_data)[0].entity("topup_kwh")
    holders = [
        card
        for card in _all_cards(build_dashboard(hass, entry.runtime_data, LAYOUT_HACS))
        if any(
            (row.get("entity") if isinstance(row, dict) else row) == topup
            for row in card.get("entities", [])
        )
    ]
    assert holders
    assert all(card["type"] == "entities" for card in holders)


async def test_analysis_charts_answer_different_questions(
    hass: HomeAssistant,
) -> None:
    """Grafik analisa untuk toko: kapan bebannya berat, dan tren bulanannya."""
    from custom_components.pln_prepaid_monitor.dashboard import build_dashboard

    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    cards = _all_cards(build_dashboard(hass, entry.runtime_data))

    # Profil daya sepanjang hari: memperlihatkan jam sibuk dan beban yang
    # tertinggal menyala.
    assert any(card["type"] == "history-graph" for card in cards)

    periods = {
        card.get("period")
        for card in cards
        if card["type"] == "statistics-graph"
    }
    assert periods == {"day", "hour", "month"}


async def test_the_notification_test_button_is_on_the_page(
    hass: HomeAssistant,
) -> None:
    """Tombol uji notifikasi hanya berguna kalau mudah ditemukan."""
    from custom_components.pln_prepaid_monitor.dashboard import (
        build_dashboard,
        collect_views,
    )

    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    button = collect_views(hass, entry.runtime_data)[0].entity("test_notification")
    assert button

    rows = {
        row["entity"] if isinstance(row, dict) else row
        for card in _all_cards(build_dashboard(hass, entry.runtime_data))
        for row in card.get("entities", [])
    }
    assert button in rows
