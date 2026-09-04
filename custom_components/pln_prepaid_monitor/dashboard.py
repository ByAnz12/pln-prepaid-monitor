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

from .const import DOMAIN
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
        "howto_title": "Cara mencatat pengisian token",
        "howto": (
            "Buka **Developer Tools -> Actions**, cari **Catat pengisian token**, "
            "pilih perangkat kelompok tagihan ini, lalu isi berapa kWh yang masuk "
            "menurut struk atau layar meteran.\n\n"
            "Kalau angka sistem mulai melenceng dari layar meteran, pakai "
            "**Samakan dengan angka meteran**."
        ),
        "hold_explain": (
            "Meteran ter-reset dan angka sesudahnya cukup besar, jadi sisa token "
            "dibekukan supaya tidak hangus salah. Pilih salah satu di bawah, atau "
            "buka **Developer Tools -> Actions -> Putuskan penahanan token** untuk "
            "memasukkan angka dari layar meteran."
        ),
        "hold_ignore": "Abaikan (meteran diganti)",
        "hold_accept": "Anggap pemakaian nyata",
        "topup_title": "Isi token",
        "topup_none": (
            "Belum ada tombol pengisian di sini karena sistem belum tahu berapa "
            "kWh yang biasa Anda beli.\n\n"
            "Ada dua cara memunculkannya:\n\n"
            "1. **Catat satu pengisian dulu** lewat **Developer Tools -> Actions "
            "-> Catat pengisian token**. Setelah itu jalankan **Buatkan "
            "dashboard** lagi, dan nilai tadi langsung jadi tombol di sini.\n"
            "2. Atau isi **Nilai pengisian siap pakai** di **Settings -> Devices "
            "& Services -> PLN Prepaid Energy & Cost Monitor -> Configure** pada "
            "kelompok ini. Cukup tulis angka kWh-nya saja, misalnya `826,50`."
        ),
        "maint_title": "Perawatan",
        "maint_note": (
            "Menghapus riwayat lama milik integrasi ini sesuai batas retensi "
            "yang Anda atur di **Configure**. Bersifat **permanen**. Entity "
            "dan data lain di Home Assistant Anda tidak tersentuh."
        ),
        "maint_button": "Bersihkan data lama",
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
        "howto_title": "How to record a top-up",
        "howto": (
            "Open **Developer Tools -> Actions**, find **Record token top-up**, "
            "pick this billing group's device, then enter how many kWh went in "
            "according to the receipt or the meter display.\n\n"
            "If the system's figure drifts from the meter, use **Match the meter "
            "reading**."
        ),
        "hold_explain": (
            "The meter reset and the value afterwards was large, so the remaining "
            "token has been frozen to avoid writing it off by mistake. Pick one "
            "below, or open **Developer Tools -> Actions -> Decide on a token "
            "hold** to enter the figure from the meter."
        ),
        "hold_ignore": "Ignore (meter replaced)",
        "hold_accept": "Treat as real usage",
        "topup_title": "Top up token",
        "topup_none": (
            "No top-up buttons here yet, because the system does not know how "
            "many kWh you usually buy.\n\n"
            "Two ways to get them:\n\n"
            "1. **Record one top-up first** via **Developer Tools -> Actions -> "
            "Record token top-up**. Then run **Generate dashboard** again and "
            "that amount becomes a button here.\n"
            "2. Or fill in **Ready-to-use top-up values** under **Settings -> "
            "Devices & Services -> PLN Prepaid Energy & Cost Monitor -> "
            "Configure** for this group. Just the kWh figure is enough, for "
            "example `826.50`."
        ),
        "maint_title": "Maintenance",
        "maint_note": (
            "Deletes this integration's old history according to the retention "
            "limit you set under **Configure**. This is **permanent**. Your "
            "other entities and data are not touched."
        ),
        "maint_button": "Purge old data",
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
        for platform in ("sensor", "binary_sensor"):
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
    "average_daily_usage",
    "days_remaining",
    "empty_date",
    "data_sufficient",
    "ledger_hold",
]

SOURCE_KEYS = ["energy", "power", "voltage", "current", "frequency", "available"]


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


def _status_card(view: GroupView, texts: dict[str, str]) -> dict[str, Any] | None:
    """Kartu ringkasan status paling atas."""
    rows = [
        entity
        for key in ("token_status", "days_remaining", "empty_date", "data_sufficient")
        if (entity := view.entity(key))
    ]
    if not rows:
        return None
    return {"type": "entities", "title": texts["status"], "entities": rows}


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

    glance = [entity for key in ("energy_total",) if (entity := view.entity(key))]
    for source in view.sources:
        glance.extend(
            entity
            for key in ("voltage", "current", "frequency", "available")
            if (entity := source.entities.get(key))
        )
    if glance:
        cards.append(
            {"type": "glance", "title": texts["sources"], "entities": glance}
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
        entity
        for key in (
            "token_remaining",
            "token_remaining_value",
            "token_consumed",
            "average_daily_usage",
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

    if view.token_enabled and view.device_id:
        # Kartu ini selalu ada kalau token dicatat. Kalau belum ada nilai yang
        # bisa dijadikan tombol, yang tampil adalah cara memunculkannya - bukan
        # ruang kosong yang membuat user mengira fiturnya tidak ada.
        cards.append({"type": "markdown", "content": f"### {texts['topup_title']}"})
        if view.presets:
            cards.append(
                {
                    "type": "horizontal-stack",
                    "cards": [
                        _topup_button(view, preset) for preset in view.presets[:4]
                    ],
                }
            )
        else:
            cards.append({"type": "markdown", "content": texts["topup_none"]})

    cards.append(
        {
            "type": "markdown",
            "content": f"### {texts['howto_title']}\n\n{texts['howto']}",
        }
    )
    return cards


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
    if status := _status_card(view, texts):
        cards.append(status)
    cards.extend(_current_cards(view, texts))

    if energy_periods := _period_card(view, "energy", texts["current"], labels):
        cards.append(energy_periods)
    if view.has_cost and (
        cost_periods := _period_card(view, "cost", texts["cost"], labels)
    ):
        cards.append(cost_periods)

    cards.extend(_token_cards(view, texts))
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
