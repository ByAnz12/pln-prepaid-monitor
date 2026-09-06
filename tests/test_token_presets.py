"""Test nilai pengisian siap pakai.

Angka di test ini diambil dari struk pembelian nyata milik user: nominal
Rp 1.000.000 menghasilkan "82650 KWM" di struk, yaitu **826,50 kWh** di layar
meteran.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pln_prepaid_monitor.const import (
    ATTR_TOTAL_CREDITED,
    CONF_CYCLE_PERIODS,
    CONF_ENERGY_ENTITY_ID,
    CONF_SOURCE_IDS,
    CONF_TOKEN_ENABLED,
    CONF_TOKEN_PRESETS,
    DOMAIN,
    SERVICE_ADD_TOKEN_TOPUP,
    SUBENTRY_TYPE_BILLING_GROUP,
    SUBENTRY_TYPE_ENERGY_SOURCE,
)
from custom_components.pln_prepaid_monitor.engines.token_engine import (
    TokenPreset,
    find_preset,
    format_presets,
    implausible_kwh_hint,
    load_presets,
    parse_kwh,
    parse_presets,
    parse_rupiah,
)

from custom_components.pln_prepaid_monitor.dashboard import view_cards

from .conftest import apply_states, MCB_RUMAH

RUMAH_ID = "src_rumah"
GROUP_ID = "grp_rumah"

# Persis seperti di struk: Rp 1.000.000 -> 826,50 kWh.
STRUK_NOMINAL = 1000000.0
STRUK_KWH = 826.50


# --- pembacaan angka ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.000.000", 1000000.0),
        ("Rp 1.000.000", 1000000.0),
        ("1000000", 1000000.0),
        (" 500.000 ", 500000.0),
        ("bukan angka", None),
    ],
)
def test_parse_rupiah(text: str, expected: float | None) -> None:
    """Nominal rupiah selalu bulat, jadi cukup diambil angkanya."""
    assert parse_rupiah(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("826,50", 826.50),
        ("826.50", 826.50),
        ("1.234,56", 1234.56),
        ("826", 826.0),
        ("", None),
        ("abc", None),
    ],
)
def test_parse_kwh_accepts_both_styles(text: str, expected: float | None) -> None:
    """Gaya Indonesia (826,50) dan Inggris (826.50) sama-sama diterima."""
    assert parse_kwh(text) == expected


# --- daftar preset -----------------------------------------------------------


def test_parse_presets_from_the_real_receipt() -> None:
    """Baris seperti di struk terbaca benar."""
    presets, bad = parse_presets("1.000.000 = 826,50")

    assert bad == []
    assert len(presets) == 1
    assert presets[0].nominal_rp == pytest.approx(STRUK_NOMINAL)
    assert presets[0].kwh == pytest.approx(STRUK_KWH)


def test_parse_presets_handles_several_lines() -> None:
    """Beberapa nominal bisa didaftarkan sekaligus."""
    presets, bad = parse_presets(
        "1.000.000 = 826,50\n500.000 = 413,25\n\n# catatan\n200000 = 165.30"
    )

    assert bad == []
    assert [preset.nominal_rp for preset in presets] == [1000000, 500000, 200000]


def test_parse_presets_reports_bad_lines_instead_of_dropping_them() -> None:
    """Baris yang salah format dilaporkan, bukan dibuang diam-diam."""
    presets, bad = parse_presets("1.000.000 = 826,50\nsalah\n= 100\n300000 =")

    assert len(presets) == 1
    assert bad == ["salah", "= 100", "300000 ="]


def test_format_presets_round_trips() -> None:
    """Teks yang ditampilkan lagi ke user bisa dibaca ulang dengan hasil sama."""
    presets, _ = parse_presets("1.000.000 = 826,50\n500.000 = 413,25")
    text = format_presets([preset.as_dict() for preset in presets])

    assert text == "1.000.000 = 826,50\n500.000 = 413,25"
    again, bad = parse_presets(text)
    assert bad == []
    assert [preset.kwh for preset in again] == [826.50, 413.25]


def test_preset_label_is_readable() -> None:
    """Label tombol memakai penulisan angka gaya Indonesia."""
    assert (
        TokenPreset(kwh=STRUK_KWH, nominal_rp=STRUK_NOMINAL).label
        == "Rp 1.000.000 (826,50 kWh)"
    )
    # Tanpa nominal, labelnya cukup angka kWh-nya saja.
    assert TokenPreset(kwh=STRUK_KWH).label == "826,50 kWh"


def test_find_preset_matches_the_amount() -> None:
    """Preset dicari berdasarkan nominal pembelian."""
    presets = [
        TokenPreset(kwh=STRUK_KWH, nominal_rp=STRUK_NOMINAL),
        TokenPreset(kwh=413.25, nominal_rp=500000),
        TokenPreset(kwh=100.0),
    ]

    assert find_preset(presets, 1000000).kwh == pytest.approx(STRUK_KWH)
    assert find_preset(presets, 500000).kwh == pytest.approx(413.25)
    assert find_preset(presets, 750000) is None
    assert find_preset(presets, None) is None


def test_load_presets_skips_broken_entries() -> None:
    """Data rusak dilewati, tidak menjatuhkan integrasi."""
    presets = load_presets(
        [{"nominal_rp": 1000000, "kwh": 826.5}, {"nominal_rp": "x"}, {}]
    )
    assert len(presets) == 1


# --- pagar salah satuan ------------------------------------------------------


def test_receipt_unit_mistake_is_caught() -> None:
    """82650 dari struk dikenali sebagai salah satuan, dengan saran 826,50."""
    assert implausible_kwh_hint(82650) == pytest.approx(826.50)
    # Angka yang wajar dibiarkan lewat.
    assert implausible_kwh_hint(826.50) is None
    assert implausible_kwh_hint(2000) is None


# --- pemakaian lewat layanan -------------------------------------------------


def _group(presets: list[dict] | None = None) -> dict:
    """Kelompok tagihan dengan token dan preset."""
    return {
        "data": {
            "name": "PLN RUMAH",
            CONF_SOURCE_IDS: [RUMAH_ID],
            CONF_CYCLE_PERIODS: ["day"],
            CONF_TOKEN_ENABLED: True,
            CONF_TOKEN_PRESETS: presets
            if presets is not None
            else [{"nominal_rp": STRUK_NOMINAL, "kwh": STRUK_KWH}],
        },
        "subentry_id": GROUP_ID,
        "subentry_type": SUBENTRY_TYPE_BILLING_GROUP,
        "title": "PLN RUMAH",
        "unique_id": None,
    }


SOURCE_SUBENTRY = {
    "data": {
        "name": "MCB RUMAH",
        CONF_ENERGY_ENTITY_ID: "sensor.mcb_rumah_total_energy",
    },
    "subentry_id": RUMAH_ID,
    "subentry_type": SUBENTRY_TYPE_ENERGY_SOURCE,
    "title": "MCB RUMAH",
    "unique_id": None,
}


async def _setup(hass: HomeAssistant, *subentries) -> MockConfigEntry:
    """Pasang integrasi lengkap."""
    await hass.config.async_set_time_zone("Asia/Jakarta")
    entry = MockConfigEntry(domain=DOMAIN, data={}, subentries_data=list(subentries))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _topup(hass: HomeAssistant, **data) -> None:
    """Panggil layanan pencatatan pengisian."""
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, GROUP_ID)})
    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_TOKEN_TOPUP,
        {"device_id": [device.id], **data},
        blocking=True,
    )
    await hass.async_block_till_done()


async def test_topup_by_amount_uses_the_preset(hass: HomeAssistant) -> None:
    """Cukup sebut nominalnya; jumlah kWh diambil dari preset.

    Inilah alur yang dipakai user sehari-hari: beli Rp 1.000.000, sistem tahu
    itu 826,50 kWh tanpa perlu diketik ulang.
    """
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, _group())

    await _topup(hass, nominal_rp=STRUK_NOMINAL)

    state = hass.states.get("sensor.pln_rumah_token_remaining")
    assert float(state.state) == pytest.approx(STRUK_KWH)
    assert state.attributes[ATTR_TOTAL_CREDITED] == pytest.approx(STRUK_KWH)


async def test_manual_kwh_still_works(hass: HomeAssistant) -> None:
    """Mengetik jumlah kWh secara manual tetap bisa, seperti sebelumnya."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, _group())

    await _topup(hass, kwh_credited=123.45)

    assert float(hass.states.get("sensor.pln_rumah_token_remaining").state) == (
        pytest.approx(123.45)
    )


async def test_explicit_kwh_wins_over_the_preset(hass: HomeAssistant) -> None:
    """Kalau keduanya diisi, angka yang diketik user yang dipakai."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, _group())

    await _topup(hass, nominal_rp=STRUK_NOMINAL, kwh_credited=800.0)

    assert float(hass.states.get("sensor.pln_rumah_token_remaining").state) == (
        pytest.approx(800.0)
    )


async def test_unknown_amount_is_refused_with_guidance(
    hass: HomeAssistant,
) -> None:
    """Nominal tanpa preset ditolak, bukan diam-diam mencatat nol kWh."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, _group())

    with pytest.raises(ServiceValidationError):
        await _topup(hass, nominal_rp=750000)


async def test_topup_without_any_value_is_refused(hass: HomeAssistant) -> None:
    """Tanpa kWh maupun nominal, tidak ada yang bisa dicatat."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, _group())

    with pytest.raises(ServiceValidationError):
        await _topup(hass)


async def test_receipt_unit_mistake_is_refused(hass: HomeAssistant) -> None:
    """Menyalin 82650 apa adanya dari struk tertahan, dengan saran perbaikan."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, _group())

    with pytest.raises(ServiceValidationError):
        await _topup(hass, kwh_credited=82650)


async def test_presets_become_dashboard_buttons(hass: HomeAssistant) -> None:
    """Nilai siap pakai muncul sebagai tombol sekali klik di dashboard."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, _group())

    from custom_components.pln_prepaid_monitor.dashboard import build_dashboard

    def _walk(cards):
        for card in cards:
            yield card
            yield from _walk(card.get("cards", []))
            if nested := card.get("card"):
                yield nested
                yield from _walk(nested.get("cards", []))

    config = build_dashboard(hass, entry.runtime_data)
    buttons = [
        card
        for view in config["views"]
        for card in _walk(view_cards(view))
        if card["type"] == "button"
        and card["tap_action"]["perform_action"].endswith("add_token_topup")
    ]

    assert len(buttons) == 1
    button = buttons[0]
    assert button["name"] == "Rp 1.000.000 (826,50 kWh)"
    data = button["tap_action"]["data"]
    # device_id ikut di dalam data sejak D-053 - kelompok tagihannya tidak lagi
    # dikirim lewat `target`.
    assert data["device_id"]
    assert {key: data[key] for key in ("kwh_credited", "nominal_rp")} == {
        "kwh_credited": STRUK_KWH,
        "nominal_rp": STRUK_NOMINAL,
    }
    # Mengubah catatan token, jadi wajib konfirmasi dulu.
    assert "confirmation" in button["tap_action"]


async def test_no_buttons_without_presets(hass: HomeAssistant) -> None:
    """Tanpa preset, tidak ada tombol pengisian di dashboard."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, _group(presets=[]))

    from custom_components.pln_prepaid_monitor.dashboard import build_dashboard

    config = build_dashboard(hass, entry.runtime_data)
    yaml_text = str(config)
    assert "add_token_topup" not in yaml_text


# --- pengaturan lewat form ---------------------------------------------------


async def test_presets_can_be_set_in_the_form(hass: HomeAssistant) -> None:
    """Teks yang diketik user tersimpan sebagai daftar nilai siap pakai."""
    from homeassistant.config_entries import SOURCE_USER

    from custom_components.pln_prepaid_monitor.const import (
        CONF_CRITICAL_THRESHOLD_DAYS,
        CONF_DAY_START_TIME,
        CONF_MIN_DATA_POINTS,
        CONF_MONTH_START_DAY,
        CONF_NOTIFY_ENABLED,
        CONF_OUTLIER_FILTER,
        CONF_PREFERRED_WINDOW,
        CONF_RESET_HOLD_THRESHOLD_KWH,
        CONF_SAFETY_MARGIN_PERCENT,
        CONF_TOKEN_LOW_KWH_THRESHOLD,
        CONF_TOKEN_PRESETS_TEXT,
        CONF_VERY_CRITICAL_THRESHOLD_DAYS,
        CONF_WARNING_THRESHOLD_DAYS,
        CONF_WEEK_START_DAY,
        CONF_YEAR_START_MONTH,
    )

    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_BILLING_GROUP),
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "PLN RUMAH", CONF_SOURCE_IDS: [RUMAH_ID]}
    )
    assert result["step_id"] == "token"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_TOKEN_ENABLED: True,
            CONF_TOKEN_PRESETS_TEXT: "1.000.000 = 826,50",
            CONF_RESET_HOLD_THRESHOLD_KWH: 1.0,
        },
    )
    assert result["step_id"] == "prediction"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_WARNING_THRESHOLD_DAYS: 7.0,
            CONF_CRITICAL_THRESHOLD_DAYS: 3.0,
            CONF_VERY_CRITICAL_THRESHOLD_DAYS: 1.0,
            CONF_TOKEN_LOW_KWH_THRESHOLD: 0.0,
            CONF_PREFERRED_WINDOW: "7d",
            CONF_MIN_DATA_POINTS: 3,
            CONF_OUTLIER_FILTER: "median",
            CONF_SAFETY_MARGIN_PERCENT: 10.0,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_NOTIFY_ENABLED: False}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_CYCLE_PERIODS: ["day"],
            CONF_DAY_START_TIME: "00:00:00",
            CONF_WEEK_START_DAY: "monday",
            CONF_MONTH_START_DAY: 1,
            CONF_YEAR_START_MONTH: "january",
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {}
    )

    group = next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_BILLING_GROUP
    )
    assert group.data[CONF_TOKEN_PRESETS] == [
        {"nominal_rp": STRUK_NOMINAL, "kwh": STRUK_KWH, "name": None}
    ]


async def test_bad_preset_format_is_rejected_in_the_form(
    hass: HomeAssistant,
) -> None:
    """Format yang salah ditolak di form, bukan diam-diam membuang barisnya."""
    from homeassistant.config_entries import SOURCE_USER

    from custom_components.pln_prepaid_monitor.const import (
        CONF_RESET_HOLD_THRESHOLD_KWH,
        CONF_TOKEN_PRESETS_TEXT,
    )

    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_BILLING_GROUP),
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "PLN RUMAH", CONF_SOURCE_IDS: [RUMAH_ID]}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_TOKEN_ENABLED: True,
            CONF_TOKEN_PRESETS_TEXT: "1.000.000 826,50",
            CONF_RESET_HOLD_THRESHOLD_KWH: 1.0,
        },
    )

    assert result["step_id"] == "token"
    assert result["errors"] == {"base": "preset_format_invalid"}


# --- tombol muncul tanpa perlu mengatur apa pun -----------------------------


def test_a_bare_kwh_line_is_enough() -> None:
    """Menulis angka kWh saja sah - tidak semua orang peduli nominalnya."""
    presets, bad = parse_presets("826,50\n413,25")

    assert bad == []
    assert [preset.kwh for preset in presets] == [826.50, 413.25]
    assert all(preset.nominal_rp is None for preset in presets)
    # Ditulis kembali apa adanya, bukan dipaksa jadi bentuk "nominal = kWh".
    assert format_presets([preset.as_dict() for preset in presets]) == "826,50\n413,25"


def test_forgetting_the_equals_sign_is_still_rejected() -> None:
    """Regresi: baris tanpa "=" tidak boleh terbaca sebagai satu angka raksasa.

    Sejak baris berisi kWh saja diterima, "1.000.000 826,50" berisiko terbaca
    sebagai 1.000.000.826,50 kWh. Salah ketik harus ditolak, bukan diam-diam
    diterima sebagai angka yang mustahil.
    """
    presets, bad = parse_presets("1.000.000 826,50")

    assert presets == []
    assert bad == ["1.000.000 826,50"]


@pytest.mark.parametrize("text", ["82650", "1.000.000 = 82650"])
def test_receipt_unit_mistake_is_rejected_when_configuring(text: str) -> None:
    """Angka satuan KWM dari struk ditolak saat diatur, bukan saat tombol ditekan."""
    presets, bad = parse_presets(text)

    assert presets == []
    assert bad == [text]


def test_history_supplies_values_the_user_actually_used() -> None:
    """Nilai diambil dari riwayat: terbaru dulu, tanpa pengulangan."""
    from custom_components.pln_prepaid_monitor.engines.token_engine import (
        presets_from_history,
    )

    entries = [
        {"kind": "topup", "kwh_credited": STRUK_KWH, "nominal_rp": None},
        {"kind": "calibrate", "kwh_credited": 999.0},
        {"kind": "topup", "kwh_credited": 413.25, "nominal_rp": 500000.0},
        {"kind": "topup", "kwh_credited": STRUK_KWH, "nominal_rp": STRUK_NOMINAL},
    ]

    presets = presets_from_history(entries)

    assert [preset.kwh for preset in presets] == [STRUK_KWH, 413.25]
    # Nilai yang sama muncul dua kali; yang punya nominal yang dipakai.
    assert presets[0].nominal_rp == STRUK_NOMINAL
    # Kalibrasi bukan pengisian, jadi tidak ikut.
    assert 999.0 not in [preset.kwh for preset in presets]


def _walk_cards(cards):
    """Ratakan seluruh kartu, termasuk yang bersarang di dalam stack."""
    for card in cards:
        yield card
        yield from _walk_cards(card.get("cards", []))
        if nested := card.get("card"):
            yield nested
            yield from _walk_cards(nested.get("cards", []))


def _topup_buttons(hass: HomeAssistant, runtime_data) -> list[dict]:
    """Semua tombol pencatatan pengisian di dashboard."""
    from custom_components.pln_prepaid_monitor.dashboard import build_dashboard

    config = build_dashboard(hass, runtime_data)
    return [
        card
        for view in config["views"]
        for card in _walk_cards(view_cards(view))
        if card["type"] == "button"
        and card["tap_action"]["perform_action"].endswith("add_token_topup")
    ]


async def test_one_topup_is_enough_to_get_a_button(hass: HomeAssistant) -> None:
    """Tanpa mengatur apa pun, satu pengisian sudah memunculkan tombolnya.

    Inilah yang membuat fitur ini bisa ditemukan: kebanyakan orang tidak akan
    pernah membuka pengaturan untuk mengisi nilai siap pakai lebih dulu.
    """
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, _group(presets=[]))

    assert _topup_buttons(hass, entry.runtime_data) == []

    await _topup(hass, kwh_credited=STRUK_KWH, nominal_rp=STRUK_NOMINAL)

    buttons = _topup_buttons(hass, entry.runtime_data)
    assert len(buttons) == 1
    assert buttons[0]["name"] == "Rp 1.000.000 (826,50 kWh)"
    assert buttons[0]["tap_action"]["data"]["kwh_credited"] == STRUK_KWH


async def test_manual_entry_works_without_any_preset(hass: HomeAssistant) -> None:
    """Tanpa satu pun nilai siap pakai, pengisian tetap bisa dilakukan di dashboard.

    Dulu bagian ini hanya berisi petunjuk menuju Developer Tools. Sekarang
    isian angka dan tombolnya ada langsung di halaman, jadi tidak ada kondisi
    apa pun di mana user terpaksa keluar dari dashboard untuk mengisi token.
    """
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, _group(presets=[]))

    from custom_components.pln_prepaid_monitor.dashboard import (
        build_dashboard,
        collect_views,
    )

    view = collect_views(hass, entry.runtime_data)[0]
    entities = {
        row["entity"] if isinstance(row, dict) else row
        for page in build_dashboard(hass, entry.runtime_data)["views"]
        for card in _walk_cards(view_cards(page))
        for row in card.get("entities", [])
    }

    # Isian jumlah kWh dan tombol pencatatnya ada di halaman, bukan sekadar ada
    # sebagai entity di suatu tempat.
    assert view.entity("topup_kwh") in entities
    assert view.entity("record_topup") in entities


async def test_configured_values_come_before_remembered_ones(
    hass: HomeAssistant,
) -> None:
    """Yang diatur user didahulukan - itu keputusan sadar mereka."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, _group())

    await _topup(hass, kwh_credited=100.0)

    names = [button["name"] for button in _topup_buttons(hass, entry.runtime_data)]
    assert names == ["Rp 1.000.000 (826,50 kWh)", "100,00 kWh"]
