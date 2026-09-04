"""Pembuat konfigurasi dashboard Lovelace dari kelompok tagihan yang ada.

Kenapa dibuat, bukan sekadar disediakan sebagai contoh untuk disalin: entity_id
di dashboard bergantung pada nama kelompok tagihan dan sumber energi yang Anda
pilih sendiri. Contoh statis berarti Anda harus mengganti belasan entity_id
secara manual, dan satu salah ketik membuat kartunya kosong tanpa penjelasan.

Semua kartu yang dipakai adalah **kartu bawaan Home Assistant**. Dashboard ini
berfungsi penuh tanpa HACS, tanpa Mushroom, tanpa kartu pihak ketiga mana pun -
sesuai prinsip "jangan membuat dependency yang tidak diperlukan" di spec J.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import CONF_TARIFF_ID, DOMAIN
from .engines.token_engine import presets_from_history
from .messages import PERIOD_LABELS, pick_language

# Judul bagian, mengikuti empat seksi yang diminta di spec J.
SECTION_TITLES: dict[str, dict[str, str]] = {
    "id": {
        "status": "Status",
        "current": "Sekarang",
        "cost": "Biaya",
        "token": "Token",
        "history": "Riwayat",
        "sources": "Sumber energi",
        "hold_title": "Pencatatan token ditahan",
        "hold_explain": (
            "Meteran ter-reset dan angka sesudahnya cukup besar, jadi sisa token "
            "dibekukan supaya tidak hangus salah. Pilih salah satu di bawah, atau "
            "buka **Developer Tools -> Actions -> Putuskan penahanan token** untuk "
            "memasukkan angka dari layar meteran."
        ),
        "hold_ignore": "Abaikan (meteran diganti)",
        "hold_accept": "Anggap pemakaian nyata",
        "topup_title": "Isi token",
        "maint_title": "Perawatan",
        "maint_note": (
            "Menghapus riwayat lama milik integrasi ini sesuai batas retensi "
            "yang Anda atur di **Configure**. Bersifat **permanen**. Entity "
            "dan data lain di Home Assistant Anda tidak tersentuh."
        ),
        "maint_button": "Bersihkan data lama",
        "status_waiting": (
            "**Perkiraan belum bisa dihitung.** Sistem butuh beberapa hari data "
            "pemakaian dulu sebelum bisa menebak kapan token habis, jadi status "
            "dan tanggal habis masih kosong. Ini normal untuk pemasangan baru - "
            "tidak ada yang rusak, dan sisa token di bawah tetap dihitung benar."
        ),
        "topup_amount": "Jumlah kWh",
        "topup_record": "Catat pengisian",
        "presets_title": "Nilai siap pakai",
        "fix_title": "Perbaiki hitungan",
        "fix_note": (
            "Dipakai kalau angka sistem sudah melenceng dari layar meteran. "
            "Isi angka yang tertera di meteran, lalu tekan **Samakan**."
        ),
        "meter_reading": "Angka di layar meteran",
        "calibrate": "Samakan",
        "reset_button": "Reset sisa token ke nol",
        "reset_note": (
            "Mulai pencatatan token dari nol. Seluruh pengisian yang masih "
            "aktif dianggap sudah tidak berlaku. **Tidak bisa dibatalkan.**"
        ),
        "settings_title": "Pengaturan",
        "rate": "Tarif per kWh",
        "meter": "Angka meteran",
        "voltage": "Tegangan",
        "current": "Arus",
        "frequency": "Frekuensi",
        "connection": "Koneksi",
        "status_token": "Status",
        "days_remaining": "Perkiraan hari tersisa",
        "empty_date": "Perkiraan tanggal habis",
        "data_sufficient": "Data cukup untuk perkiraan",
        "token_remaining": "Sisa token",
        "token_value": "Nilai sisa token",
        "token_consumed": "Terpakai dari token",
        "avg_daily": "Rata-rata harian",
        "energy_history": "Pemakaian harian",
        "cost_history": "Biaya harian",
    },
    "en": {
        "status": "Status",
        "current": "Right now",
        "cost": "Cost",
        "token": "Token",
        "history": "History",
        "sources": "Energy sources",
        "hold_title": "Token tracking on hold",
        "hold_explain": (
            "The meter reset and the value afterwards was large, so the remaining "
            "token has been frozen to avoid writing it off by mistake. Pick one "
            "below, or open **Developer Tools -> Actions -> Decide on a token "
            "hold** to enter the figure from the meter."
        ),
        "hold_ignore": "Ignore (meter replaced)",
        "hold_accept": "Treat as real usage",
        "topup_title": "Top up token",
        "maint_title": "Maintenance",
        "maint_note": (
            "Deletes this integration's old history according to the retention "
            "limit you set under **Configure**. This is **permanent**. Your "
            "other entities and data are not touched."
        ),
        "maint_button": "Purge old data",
        "status_waiting": (
            "**No estimate yet.** The system needs a few days of usage data "
            "before it can guess when the token runs out, so the status and "
            "empty date are still blank. This is normal for a fresh install - "
            "nothing is broken, and the remaining token below is still correct."
        ),
        "topup_amount": "Amount in kWh",
        "topup_record": "Record top-up",
        "presets_title": "Ready-to-use values",
        "fix_title": "Correct the figure",
        "fix_note": (
            "Use this when the system's figure has drifted from the meter "
            "display. Enter the figure shown on the meter, then press **Match**."
        ),
        "meter_reading": "Figure on the meter",
        "calibrate": "Match",
        "reset_button": "Reset remaining token to zero",
        "reset_note": (
            "Start token tracking from zero. Every top-up still counted is "
            "treated as no longer valid. **This cannot be undone.**"
        ),
        "settings_title": "Settings",
        "rate": "Rate per kWh",
        "meter": "Meter reading",
        "voltage": "Voltage",
        "current": "Current",
        "frequency": "Frequency",
        "connection": "Connection",
        "status_token": "Status",
        "days_remaining": "Estimated days remaining",
        "empty_date": "Estimated empty date",
        "data_sufficient": "Data sufficient",
        "token_remaining": "Remaining token",
        "token_value": "Value of remaining token",
        "token_consumed": "Used from token",
        "avg_daily": "Daily average",
        "energy_history": "Daily usage",
        "cost_history": "Daily cost",
    },
}


@dataclass
class SourceView:
    """entity_id milik satu sumber energi, sudah diselesaikan."""

    name: str
    entities: dict[str, str] = field(default_factory=dict)


@dataclass
class GroupView:
    """Semua yang dibutuhkan untuk menyusun satu halaman kelompok tagihan."""

    name: str
    device_id: str | None
    periods: list[str]
    has_cost: bool
    token_enabled: bool
    presets: list[Any] = field(default_factory=list)
    entities: dict[str, str] = field(default_factory=dict)
    tariff_entities: dict[str, str] = field(default_factory=dict)
    sources: list[SourceView] = field(default_factory=list)

    def entity(self, key: str) -> str | None:
        """entity_id untuk satu peran, kalau entity-nya memang dibuat."""
        return self.entities.get(key)


def _usable_presets(group: Any) -> list[Any]:
    """Nilai yang bisa dijadikan tombol: yang diatur user, lalu yang pernah dipakai.

    Kebanyakan orang tidak akan pernah membuka pengaturan untuk mengisi nilai
    siap pakai - mereka baru tahu fiturnya ada setelah butuh. Jadi riwayat
    pengisian mereka sendiri ikut dipakai: begitu satu pengisian tercatat,
    tombolnya muncul sendiri tanpa mengatur apa pun.

    Yang diatur user didahulukan, karena itu keputusan sadar mereka.
    """
    presets = list(group.token_presets)
    known = {round(preset.kwh, 2) for preset in presets}
    for preset in presets_from_history(group.ledger.state.entries):
        if round(preset.kwh, 2) in known:
            continue
        known.add(round(preset.kwh, 2))
        presets.append(preset)
    return presets


def _slugify(value: str) -> str:
    """Ubah nama jadi path halaman yang aman."""
    return "".join(
        char if char.isalnum() else "-" for char in value.lower()
    ).strip("-") or "pln"


def _resolve(hass: HomeAssistant, subentry_id: str, keys: list[str]) -> dict[str, str]:
    """Cari entity_id dari unique_id, hanya untuk entity yang benar-benar ada."""
    registry = er.async_get(hass)
    resolved: dict[str, str] = {}
    for key in keys:
        for platform in ("sensor", "binary_sensor", "number", "button"):
            entity_id = registry.async_get_entity_id(
                platform, DOMAIN, f"{subentry_id}_{key}"
            )
            if entity_id:
                resolved[key] = entity_id
                break
    return resolved


GROUP_KEYS = [
    "energy_total",
    "power",
    "cost_total",
    "token_remaining",
    "token_remaining_value",
    "token_consumed",
    "token_status",
    "avg_daily_usage",
    "days_remaining",
    "empty_date",
    "data_sufficient",
    "ledger_hold",
    # Isian dan tombol, supaya seluruh pencatatan token bisa dilakukan dari
    # dashboard tanpa membuka Developer Tools.
    "topup_kwh",
    "meter_reading_kwh",
    "record_topup",
    "calibrate_token",
    "warning_threshold_days",
    "critical_threshold_days",
    "very_critical_threshold_days",
]

SOURCE_KEYS = ["energy", "power", "voltage", "current", "frequency", "available"]

# Tarif punya perangkatnya sendiri, jadi entity-nya dicari terpisah.
TARIFF_KEYS = ["rate_rp_per_kwh"]


def collect_views(hass: HomeAssistant, runtime_data: Any) -> list[GroupView]:
    """Kumpulkan entity_id nyata untuk setiap kelompok tagihan."""
    from homeassistant.helpers import device_registry as dr  # noqa: PLC0415

    device_registry = dr.async_get(hass)
    views: list[GroupView] = []

    for subentry_id, group in runtime_data.billing_groups.items():
        keys = list(GROUP_KEYS)
        keys.extend(f"energy_this_{period}" for period in group.periods)
        keys.extend(f"cost_this_{period}" for period in group.periods)

        device = device_registry.async_get_device(identifiers={(DOMAIN, subentry_id)})
        views.append(
            GroupView(
                name=group.name,
                device_id=device.id if device else None,
                periods=list(group.periods),
                has_cost=group.has_cost,
                token_enabled=group.token_enabled,
                presets=_usable_presets(group),
                entities=_resolve(hass, subentry_id, keys),
                tariff_entities=(
                    _resolve(hass, tariff_id, TARIFF_KEYS)
                    if (tariff_id := group.config.get(CONF_TARIFF_ID))
                    else {}
                ),
                sources=[
                    SourceView(
                        name=source.name,
                        entities=_resolve(hass, source_id, SOURCE_KEYS),
                    )
                    for source_id, source in group.members.items()
                ],
            )
        )
    return views


STATUS_ROWS = (
    ("token_status", "status_token"),
    ("days_remaining", "days_remaining"),
    ("empty_date", "empty_date"),
    ("data_sufficient", "data_sufficient"),
)


def _status_cards(view: GroupView, texts: dict[str, str]) -> list[dict[str, Any]]:
    """Ringkasan status, plus penjelasan kalau perkiraannya memang belum ada.

    Tanpa penjelasan itu, pemasangan baru menampilkan empat baris berisi
    "Unknown" dan "Unavailable" - terlihat seperti rusak, padahal sistem hanya
    belum punya cukup data. Nama barisnya juga dipendekkan; tanpa itu setiap
    baris diawali nama kelompok tagihan dan terpotong di layar sempit.
    """
    rows = [
        {"entity": entity, "name": texts[label]}
        for key, label in STATUS_ROWS
        if (entity := view.entity(key))
    ]
    if not rows:
        return []

    cards: list[dict[str, Any]] = [
        {"type": "entities", "title": texts["status"], "entities": rows}
    ]
    if sufficient := view.entity("data_sufficient"):
        cards.append(
            {
                "type": "conditional",
                "conditions": [{"entity": sufficient, "state": "off"}],
                "card": {"type": "markdown", "content": texts["status_waiting"]},
            }
        )
    return cards


def _current_cards(view: GroupView, texts: dict[str, str]) -> list[dict[str, Any]]:
    """Daya sekarang, energi total, dan pembacaan per sumber."""
    cards: list[dict[str, Any]] = []

    if power := view.entity("power"):
        cards.append(
            {
                "type": "gauge",
                "entity": power,
                "name": texts["current"],
                "needle": True,
                "min": 0,
                "max": 5000,
            }
        )

    # Satu kartu per sumber, dengan nama pendek. Sebelumnya semua sumber
    # dijejalkan ke satu kartu memakai nama panjang bawaan entity, sehingga
    # labelnya terpotong jadi "MCB TOKO ..." dan tidak terbaca sama sekali.
    for source in view.sources:
        entities = [
            {"entity": entity, "name": texts[label]}
            for key, label in (
                ("energy", "meter"),
                ("voltage", "voltage"),
                ("current", "current"),
                ("frequency", "frequency"),
                ("available", "connection"),
            )
            if (entity := source.entities.get(key))
        ]
        if entities:
            cards.append(
                {
                    "type": "glance",
                    "title": f"{texts['sources']}: {source.name}",
                    "entities": entities,
                }
            )

    return cards


def _period_card(
    view: GroupView, prefix: str, title: str, labels: dict[str, str]
) -> dict[str, Any] | None:
    """Kartu daftar penghitung per periode (energi atau biaya)."""
    rows = [
        {"entity": entity, "name": labels.get(period, period)}
        for period in view.periods
        if (entity := view.entity(f"{prefix}_this_{period}"))
    ]
    if not rows:
        return None
    return {"type": "entities", "title": title, "entities": rows}


def _token_cards(view: GroupView, texts: dict[str, str]) -> list[dict[str, Any]]:
    """Kartu sisa token, penahanan, dan petunjuk mencatat pengisian."""
    if not view.token_enabled:
        return []

    cards: list[dict[str, Any]] = []
    rows = [
        {"entity": entity, "name": texts[label]}
        for key, label in (
            ("token_remaining", "token_remaining"),
            ("token_remaining_value", "token_value"),
            ("token_consumed", "token_consumed"),
            ("avg_daily_usage", "avg_daily"),
        )
        if (entity := view.entity(key))
    ]
    if rows:
        cards.append({"type": "entities", "title": texts["token"], "entities": rows})

    hold_entity = view.entity("ledger_hold")
    if hold_entity and view.device_id:
        # Tombol keputusan hanya muncul saat memang sedang ditahan, dan tetap
        # meminta konfirmasi karena keduanya mengubah catatan token.
        cards.append(
            {
                "type": "conditional",
                "conditions": [{"entity": hold_entity, "state": "on"}],
                "card": {
                    "type": "vertical-stack",
                    "cards": [
                        {
                            "type": "markdown",
                            "content": (
                                f"### {texts['hold_title']}\n\n"
                                f"{texts['hold_explain']}"
                            ),
                        },
                        {
                            "type": "entities",
                            "entities": [hold_entity],
                        },
                        {
                            "type": "horizontal-stack",
                            "cards": [
                                _hold_button(
                                    view, texts["hold_ignore"], "ignore", "mdi:close"
                                ),
                                _hold_button(
                                    view, texts["hold_accept"], "accept", "mdi:check"
                                ),
                            ],
                        },
                    ],
                },
            }
        )

    if topup := _topup_card(view, texts):
        cards.append(topup)
    if fix := _fix_card(view, texts):
        cards.append(fix)

    return cards


def _topup_card(view: GroupView, texts: dict[str, str]) -> dict[str, Any] | None:
    """Semua cara mengisi token, dalam satu tumpukan yang tidak bisa terpisah.

    Digabung jadi satu ``vertical-stack`` dengan sengaja: tata letak masonry
    Home Assistant menyebar kartu ke kolom mana pun yang masih kosong, sehingga
    judul dan tombolnya bisa berakhir di kolom berbeda - persis yang terjadi
    sebelum ini, di mana judul "Isi token" tampil sendirian tanpa isi apa pun.
    """
    inner: list[dict[str, Any]] = []

    rows = [
        {"entity": entity, "name": texts[label]}
        for key, label in (
            ("topup_kwh", "topup_amount"),
            ("record_topup", "topup_record"),
        )
        if (entity := view.entity(key))
    ]
    if rows:
        inner.append(
            {"type": "entities", "title": texts["topup_title"], "entities": rows}
        )

    if view.presets and view.device_id:
        inner.append({"type": "markdown", "content": f"**{texts['presets_title']}**"})
        inner.append(
            {
                "type": "grid",
                "columns": 2,
                "square": False,
                "cards": [_topup_button(view, preset) for preset in view.presets[:4]],
            }
        )

    if not inner:
        return None
    return {"type": "vertical-stack", "cards": inner}


def _fix_card(view: GroupView, texts: dict[str, str]) -> dict[str, Any] | None:
    """Penyamaan dengan meteran dan reset ledger, jadi satu tumpukan."""
    inner: list[dict[str, Any]] = []

    rows = [
        {"entity": entity, "name": texts[label]}
        for key, label in (
            ("meter_reading_kwh", "meter_reading"),
            ("calibrate_token", "calibrate"),
        )
        if (entity := view.entity(key))
    ]
    if rows:
        inner.append({"type": "markdown", "content": texts["fix_note"]})
        inner.append(
            {"type": "entities", "title": texts["fix_title"], "entities": rows}
        )

    if view.device_id:
        # Reset sengaja tetap tombol kartu, bukan entity button: menekan entity
        # button langsung menjalankan aksinya tanpa dialog konfirmasi, sementara
        # reset tidak bisa dibatalkan. Lihat button.py.
        inner.append({"type": "markdown", "content": texts["reset_note"]})
        inner.append(
            {
                "type": "button",
                "name": texts["reset_button"],
                "show_icon": False,
                "show_state": False,
                "tap_action": {
                    "action": "perform-action",
                    "perform_action": f"{DOMAIN}.reset_token_ledger",
                    "target": {"device_id": view.device_id},
                    "confirmation": {"text": f"{texts['reset_button']}?"},
                },
            }
        )

    if not inner:
        return None
    return {"type": "vertical-stack", "cards": inner}


def _settings_card(view: GroupView, texts: dict[str, str]) -> dict[str, Any] | None:
    """Pengaturan yang wajar berubah sesekali, bisa diubah langsung di sini."""
    rows = [
        {"entity": entity, "name": texts["rate"]}
        for key in ("rate_rp_per_kwh",)
        if (entity := view.tariff_entities.get(key))
    ]
    rows.extend(
        {"entity": entity}
        for key in (
            "warning_threshold_days",
            "critical_threshold_days",
            "very_critical_threshold_days",
        )
        if (entity := view.entity(key))
    )
    if not rows:
        return None
    return {"type": "entities", "title": texts["settings_title"], "entities": rows}


def _topup_button(view: GroupView, preset: Any) -> dict[str, Any]:
    """Tombol satu klik untuk mencatat pengisian dengan nilai siap pakai.

    Angka kWh dikirim apa adanya, tidak dicari ulang dari nominalnya. Alasannya
    kejujuran: label tombol sudah menyebut angkanya, dan dialog konfirmasi
    mengulanginya. Kalau tombol mengirim nominal saja lalu nilai kWh-nya
    berubah di pengaturan, tombol akan mencatat angka yang berbeda dari yang
    tertulis di tombol itu sendiri - persis jenis ketidakcocokan diam-diam yang
    dihindari sistem ini. Nominal tetap ikut dikirim sebagai catatan pembelian.
    """
    data: dict[str, Any] = {"kwh_credited": preset.kwh}
    if preset.nominal_rp is not None:
        data["nominal_rp"] = preset.nominal_rp
    return {
        "type": "button",
        "name": preset.label,
        "icon": "mdi:cash-plus",
        "show_state": False,
        "tap_action": {
            "action": "perform-action",
            "perform_action": f"{DOMAIN}.add_token_topup",
            "target": {"device_id": view.device_id},
            "data": data,
            "confirmation": {"text": f"{preset.label}?"},
        },
    }


def _hold_button(view: GroupView, name: str, action: str, icon: str) -> dict[str, Any]:
    """Tombol satu klik untuk melepas penahanan, dengan dialog konfirmasi."""
    return {
        "type": "button",
        "name": name,
        "icon": icon,
        "show_state": False,
        "tap_action": {
            "action": "perform-action",
            "perform_action": f"{DOMAIN}.resolve_ledger_hold",
            "target": {"device_id": view.device_id},
            "data": {"action": action},
            "confirmation": {"text": f"{name}?"},
        },
    }


def _history_cards(view: GroupView, texts: dict[str, str]) -> list[dict[str, Any]]:
    """Grafik riwayat, dibaca langsung dari long-term statistics."""
    cards: list[dict[str, Any]] = []

    if energy := view.entity("energy_total"):
        cards.append(
            {
                "type": "statistics-graph",
                "title": texts["energy_history"],
                "entities": [energy],
                "stat_types": ["change"],
                "period": "day",
                "days_to_show": 30,
                "chart_type": "bar",
            }
        )

    if cost := view.entity("cost_total"):
        cards.append(
            {
                "type": "statistics-graph",
                "title": texts["cost_history"],
                "entities": [cost],
                "stat_types": ["change"],
                "period": "day",
                "days_to_show": 30,
                "chart_type": "bar",
            }
        )

    return cards


def _maintenance_card(view: GroupView, texts: dict[str, str]) -> dict[str, Any]:
    """Kartu perawatan data, dengan tombol yang wajib dikonfirmasi.

    Ditaruh paling bawah dan dibuat sepolos mungkin: aksinya permanen, jadi
    tidak pantas duduk berdampingan dengan angka-angka yang dibaca sehari-hari.
    """
    cards: list[dict[str, Any]] = [
        {
            "type": "markdown",
            "content": f"### {texts['maint_title']}\n\n{texts['maint_note']}",
        }
    ]
    if view.device_id:
        cards.append(
            {
                "type": "button",
                "name": texts["maint_button"],
                "show_icon": False,
                "icon": "mdi:database-remove",
                "show_state": False,
                "tap_action": {
                    "action": "perform-action",
                    "perform_action": f"{DOMAIN}.purge_old_data",
                    "target": {"device_id": view.device_id},
                    "confirmation": {"text": f"{texts['maint_button']}?"},
                },
            }
        )
    return {"type": "vertical-stack", "cards": cards}


def build_view(view: GroupView, language: str) -> dict[str, Any]:
    """Susun satu halaman dashboard untuk satu kelompok tagihan."""
    texts = SECTION_TITLES[language]
    labels = PERIOD_LABELS[language]

    cards: list[dict[str, Any]] = []
    cards.extend(_status_cards(view, texts))
    cards.extend(_current_cards(view, texts))

    if energy_periods := _period_card(view, "energy", texts["current"], labels):
        cards.append(energy_periods)
    if view.has_cost and (
        cost_periods := _period_card(view, "cost", texts["cost"], labels)
    ):
        cards.append(cost_periods)

    cards.extend(_token_cards(view, texts))
    if settings := _settings_card(view, texts):
        cards.append(settings)
    cards.extend(_history_cards(view, texts))
    cards.append(_maintenance_card(view, texts))

    return {
        "title": view.name,
        "path": _slugify(view.name),
        "cards": cards,
    }


def build_dashboard(hass: HomeAssistant, runtime_data: Any) -> dict[str, Any]:
    """Susun seluruh dashboard: satu halaman per kelompok tagihan."""
    language = pick_language(hass.config.language)
    views = collect_views(hass, runtime_data)
    return {
        "views": [build_view(view, language) for view in views],
    }
