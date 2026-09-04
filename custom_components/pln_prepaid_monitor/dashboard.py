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

from .const import CONF_DETAIL_ROWS, CONF_TARIFF_ID, DOMAIN
from .engines.period_summary import selected_rows
from .engines.token_engine import presets_from_history
from .messages import PERIOD_LABELS, currency_symbol, pick_language

# Tata letak halaman. "sections" bawaan Home Assistant, dan satu-satunya
# yang mendukung geser kartu dengan drag & drop.
LAYOUT_SECTIONS = "sections"
LAYOUT_HACS = "sections_hacs"
LAYOUT_MASONRY = "masonry"
LAYOUTS = (LAYOUT_SECTIONS, LAYOUT_HACS, LAYOUT_MASONRY)

# Kartu pihak ketiga yang dipakai varian HACS. Berbeda dari seluruh bagian
# lain sistem ini, bentuk konfigurasi kartu ini TIDAK bisa diverifikasi ke
# source code: kodenya JavaScript milik pihak ketiga, bukan paket Python
# Home Assistant. Lihat docs/decisions.md D-046.
HACS_CARDS = ("mushroom", "apexcharts-card")

# Judul bagian, mengikuti empat seksi yang diminta di spec J.
SECTION_TITLES: dict[str, dict[str, str]] = {
    "id": {
        "status": "Status",
        "current": "Sekarang",
        "usage": "Pemakaian",
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
        "presets_title": "Template pengisian",
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
        "ch_meter": "Meteran",
        "ch_voltage": "Tegangan",
        "ch_current": "Arus",
        "ch_frequency": "Frekuensi",
        "ch_connection": "Koneksi",
        "status_token": "Status",
        "days_remaining": "Perkiraan hari tersisa",
        "empty_date": "Perkiraan tanggal habis",
        "data_sufficient": "Data cukup untuk perkiraan",
        "token_remaining": "Sisa token",
        "token_value": "Nilai sisa token",
        "token_consumed": "Terpakai dari token",
        "avg_daily": "Rata-rata harian",
        "topup_history_title": "Riwayat pengisian",
        "col_date": "Tanggal",
        "col_rp": "Rupiah",
        "history_rows": "Tampilkan berapa baris",
        "no_topup_yet": "Belum ada pengisian yang tercatat.",
        "superseded_note": (
            "Tanda bintang berarti pengisian itu sudah digantikan penyamaan "
            "atau reset, jadi tidak lagi ikut dihitung."
        ),
        "topup_nominal": "Nominal pembelian",
        "threshold_warning": "Ambang peringatan",
        "threshold_critical": "Ambang kritis",
        "threshold_very_critical": "Ambang sangat kritis",
        "sec_overview": "Ringkasan",
        "sec_usage": "Pemakaian & biaya",
        "sec_token": "Token",
        "sec_settings": "Pengaturan",
        "sec_graphs": "Grafik",
        "sec_maintenance": "Perawatan",
        "save_template": "Simpan sebagai template",
        "save_action": "SIMPAN",
        "rate_title": "Harga per kWh berubah",
        "rate_explain": "Pengisian terakhir menyebut jumlah kWh dan nominal sekaligus, jadi harga efektifnya bisa dihitung - sudah termasuk admin, PPJ, dan materai. Mau dipakai sebagai harga baru?",
        "rate_from": "Harga sekarang",
        "rate_to": "Harga hasil hitungan",
        "rate_basis": "Dari pembelian",
        "rate_implausible": "> **Perubahannya besar sekali.** Periksa lagi angka pengisiannya sebelum menerima - salah ketik satu angka bisa membuat seluruh hitungan biaya berikutnya meleset.",
        "rate_yes": "Ya, pakai harga baru",
        "rate_no": "Tidak, biarkan",
        "hacs_note": (
            "**Dashboard ini butuh dua kartu dari HACS.** Kalau belum "
            "terpasang, beberapa kartu di bawah akan tampil sebagai kotak "
            "merah *Custom element doesn't exist*.\n\n"
            "Pasang lewat **HACS → Frontend**:\n\n"
            "* **Mushroom** — status token dan baris ringkas sumber energi\n"
            "* **apexcharts-card** — grafik pemakaian dan biaya harian\n\n"
            "Setelah dipasang, muat ulang halaman dengan **Ctrl+Shift+R**."
        ),
        "energy_history": "Pemakaian harian",
        "cost_history": "Biaya harian",
    },
    "en": {
        "status": "Status",
        "current": "Right now",
        "usage": "Usage",
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
        "presets_title": "Top-up templates",
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
        "ch_meter": "Meter",
        "ch_voltage": "Voltage",
        "ch_current": "Current",
        "ch_frequency": "Frequency",
        "ch_connection": "Connection",
        "status_token": "Status",
        "days_remaining": "Estimated days remaining",
        "empty_date": "Estimated empty date",
        "data_sufficient": "Data sufficient",
        "token_remaining": "Remaining token",
        "token_value": "Value of remaining token",
        "token_consumed": "Used from token",
        "avg_daily": "Daily average",
        "topup_history_title": "Top-up history",
        "col_date": "Date",
        "col_rp": "Amount",
        "history_rows": "Rows to show",
        "no_topup_yet": "No top-up recorded yet.",
        "superseded_note": (
            "An asterisk means that top-up was superseded by a match or a "
            "reset, so it no longer counts."
        ),
        "topup_nominal": "Purchase amount",
        "threshold_warning": "Warning threshold",
        "threshold_critical": "Critical threshold",
        "threshold_very_critical": "Very critical threshold",
        "sec_overview": "Overview",
        "sec_usage": "Usage & cost",
        "sec_token": "Token",
        "sec_settings": "Settings",
        "sec_graphs": "Charts",
        "sec_maintenance": "Maintenance",
        "save_template": "Save as template",
        "save_action": "SAVE",
        "rate_title": "Price per kWh has changed",
        "rate_explain": "The last top-up carried both the kWh figure and the amount paid, so the effective price can be worked out - admin fees, tax and stamp duty included. Use it as the new price?",
        "rate_from": "Current price",
        "rate_to": "Calculated price",
        "rate_basis": "From purchase",
        "rate_implausible": "> **That is a very large change.** Check the top-up figures before accepting - one mistyped digit can throw off every cost figure from here on.",
        "rate_yes": "Yes, use the new price",
        "rate_no": "No, leave it",
        "hacs_note": (
            "**This dashboard needs two cards from HACS.** Without them, some "
            "cards below show up as a red *Custom element doesn't exist* box."
            "\n\nInstall them via **HACS → Frontend**:\n\n"
            "* **Mushroom** — token status and the compact source row\n"
            "* **apexcharts-card** — daily usage and cost charts\n\n"
            "Then hard-reload the page with **Ctrl+Shift+R**."
        ),
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
    detail_rows: list[str] = field(default_factory=list)
    thresholds: Any = None
    currency: str = ""
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
    "topup_rp",
    "rate_change_pending",
    "meter_reading_kwh",
    "record_topup",
    "save_template",
    "calibrate_token",
    "warning_threshold_days",
    "critical_threshold_days",
    "very_critical_threshold_days",
    "history_rows",
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
                detail_rows=selected_rows(group.config.get(CONF_DETAIL_ROWS)),
                thresholds=group.thresholds,
                currency=currency_symbol(hass.config.currency),
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

    cards: list[dict[str, Any]] = []

    # Titik fokus halaman: satu angka besar yang langsung menjawab "masih aman
    # atau tidak". Warnanya memakai ambang yang user atur sendiri, jadi merah di
    # sini berarti persis apa yang mereka tetapkan sebagai sangat kritis.
    days = view.entity("days_remaining")
    sufficient = view.entity("data_sufficient")
    if days and sufficient and view.thresholds is not None:
        # Digantung pada "data cukup", bukan langsung ditampilkan: sebelum
        # datanya cukup, sensor hari tersisa memang belum punya nilai, dan
        # gauge yang menunjuk entity tanpa nilai memasang kartu peringatan
        # merah di puncak halaman. Peringatan itu terlihat seperti kerusakan,
        # padahal keadaannya normal untuk pemasangan baru.
        cards.append(
            {
                "type": "conditional",
                "conditions": [{"entity": sufficient, "state": "on"}],
                "card": {
                    "type": "gauge",
                    "entity": days,
                    "name": texts["days_remaining"],
                    "min": 0,
                    "max": round(float(view.thresholds.warning_days) * 3),
                    "severity": {
                        "green": float(view.thresholds.warning_days),
                        "yellow": float(view.thresholds.critical_days),
                        "red": 0,
                    },
                },
            }
        )

    cards.append({"type": "entities", "title": texts["status"], "entities": rows})
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
                ("energy", "ch_meter"),
                ("voltage", "ch_voltage"),
                ("current", "ch_current"),
                ("frequency", "ch_frequency"),
                ("available", "ch_connection"),
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
    """Rincian per periode: rata-rata, beberapa hari lalu, beberapa bulan lalu.

    Dirender dari atribut ``period_summary`` pada sensor total, bukan dari
    belasan entity terpisah. Baris seperti "3 bulan lalu" adalah angka untuk
    dibaca, bukan untuk dipakai automation; membuat entity sendiri untuk
    masing-masing berarti belasan entity baru per kelompok tagihan, lengkap
    dengan riwayatnya di database - biaya yang tidak sepadan.
    """
    total = view.entity(f"{prefix}_total")
    if not total or not view.detail_rows:
        return None

    unit = "kWh" if prefix == "energy" else ""
    lines = [f"### {title}", f"{{% set s = state_attr('{total}', 'period_summary') or {{}} %}}"]
    for row in view.detail_rows:
        value = f"s.get('{row}')"
        shown = (
            f"{{{{ '%.2f' | format({value}) }}}}{' ' + unit if unit else ''}"
            if prefix == "energy"
            else (
                f"{view.currency} "
                f"{{{{ '{{:,.0f}}'.format({value}) | replace(',', '.') }}}}"
            )
        )
        lines.append(
            f"{{% if {value} is not none %}}"
            f"| {labels.get(row, row)} | {shown} |"
            f"{{% else %}}| {labels.get(row, row)} | - |{{% endif %}}"
        )
    # Header tabel disisipkan sesudah baris set supaya template tetap terbaca.
    lines.insert(2, "| | |")
    lines.insert(3, "|---|--:|")
    return {"type": "markdown", "content": "\n".join(lines)}


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

    cards.extend(_topup_cards(view, texts))
    cards.extend(_fix_cards(view, texts))

    return cards


def _topup_cards(view: GroupView, texts: dict[str, str]) -> list[dict[str, Any]]:
    """Semua cara mengisi token: template sekali klik, atau ketik sendiri."""
    inner: list[dict[str, Any]] = []

    rows = [
        {"entity": entity, "name": texts[label]}
        for key, label in (
            ("topup_kwh", "topup_amount"),
            ("topup_rp", "topup_nominal"),
            ("record_topup", "topup_record"),
            # Cara paling alami membuat template adalah tepat sesudah mengetik
            # angkanya, bukan dengan membuka layar pengaturan terpisah.
            ("save_template", "save_template"),
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
                "cards": [_topup_button(view, preset) for preset in view.presets[:6]],
            }
        )

    inner.extend(_rate_proposal_cards(view, texts))
    return inner


def _rate_proposal_cards(
    view: GroupView, texts: dict[str, str]
) -> list[dict[str, Any]]:
    """Pertanyaan harga baru, hanya muncul saat memang ada yang ditanyakan.

    Polanya sama dengan penahanan ledger: sistem berhenti dan bertanya, bukan
    diam-diam mengubah angka yang user tetapkan sendiri.
    """
    pending = view.entity("rate_change_pending")
    if not pending or not view.device_id:
        return []

    body = (
        f"### {texts['rate_title']}"
        f"{{% set a = state_attr('{pending}', 'from_rate') %}}"
        f"{{% set b = state_attr('{pending}', 'to_rate') %}}"
        f"{{% set kwh = state_attr('{pending}', 'kwh') %}}"
        f"{{% set rp = state_attr('{pending}', 'nominal_rp') %}}"
        f"{chr(10)}{chr(10)}"
        f"{texts['rate_explain']}"
        f"{chr(10)}{chr(10)}"
        f"| | |{chr(10)}|---|--:|{chr(10)}"
        f"| {texts['rate_from']} | {view.currency} "
        f"{{{{ '{{:,.2f}}'.format(a) | replace(',', '@') | replace('.', ',') "
        f"| replace('@', '.') }}}} |{chr(10)}"
        f"| {texts['rate_to']} | **{view.currency} "
        f"{{{{ '{{:,.2f}}'.format(b) | replace(',', '@') | replace('.', ',') "
        f"| replace('@', '.') }}}}** |{chr(10)}"
        f"| {texts['rate_basis']} | {{{{ '%.2f' | format(kwh) }}}} kWh / "
        f"{view.currency} {{{{ '{{:,.0f}}'.format(rp) | replace(',', '.') }}}} |"
        f"{chr(10)}{chr(10)}"
        f"{{% if state_attr('{pending}', 'implausible') %}}"
        f"{texts['rate_implausible']}{{% endif %}}"
    )

    return [
        {
            "type": "conditional",
            "conditions": [{"entity": pending, "state": "on"}],
            "card": {
                "type": "vertical-stack",
                "cards": [
                    {"type": "markdown", "content": body},
                    {
                        "type": "horizontal-stack",
                        "cards": [
                            _rate_button(view, texts["rate_yes"], True, "mdi:check"),
                            _rate_button(view, texts["rate_no"], False, "mdi:close"),
                        ],
                    },
                ],
            },
        }
    ]


def _rate_button(
    view: GroupView, name: str, apply: bool, icon: str
) -> dict[str, Any]:
    """Satu tombol keputusan harga, tetap dengan dialog konfirmasi."""
    return {
        "type": "button",
        "name": name,
        "icon": icon,
        "show_state": False,
        "tap_action": {
            "action": "perform-action",
            "perform_action": f"{DOMAIN}.resolve_rate_change",
            "target": {"device_id": view.device_id},
            "data": {"apply": apply},
            "confirmation": {"text": f"{name}?"},
        },
    }


def _fix_cards(view: GroupView, texts: dict[str, str]) -> list[dict[str, Any]]:
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

    return inner


def _settings_card(view: GroupView, texts: dict[str, str]) -> dict[str, Any] | None:
    """Pengaturan yang wajar berubah sesekali, bisa diubah langsung di sini."""
    rows = [
        {"entity": entity, "name": texts["rate"]}
        for key in ("rate_rp_per_kwh",)
        if (entity := view.tariff_entities.get(key))
    ]
    rows.extend(
        {"entity": entity, "name": texts[label]}
        for key, label in (
            ("warning_threshold_days", "threshold_warning"),
            ("critical_threshold_days", "threshold_critical"),
            ("very_critical_threshold_days", "threshold_very_critical"),
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


def _topup_history_cards(
    view: GroupView, texts: dict[str, str]
) -> list[dict[str, Any]]:
    """Tabel riwayat pengisian, terbaru di atas, jumlah barisnya bisa diatur.

    Kartu markdown adalah satu-satunya kartu bawaan Home Assistant yang bisa
    menampilkan tabel dari data yang berubah-ubah. Template-nya sengaja pendek:
    atribut ``topup_log`` sudah disiapkan dalam bentuk siap tampil oleh
    ``token_engine.topup_log``, karena template Jinja di dashboard tidak bisa
    diuji dan sulit di-debug user kalau ada yang salah.
    """
    token = view.entity("token_remaining")
    if not token:
        return []

    rows_entity = view.entity("history_rows")
    limit = f"states('{rows_entity}') | int(10)" if rows_entity else "10"

    template = f"""### {texts["topup_history_title"]}
{{% set log = state_attr('{token}', 'topup_log') or [] %}}
{{% set rows = log[:({limit})] %}}
{{% if rows %}}
| # | {texts["col_date"]} | kWh | {texts["col_rp"]} |
|--:|---|--:|--:|
{{%- for row in rows %}}
| {{{{ row.no }}}} | {{{{ (row.at | as_datetime | as_local).strftime('%d/%m/%y %H:%M') }}}} \
| {{{{ '%.2f' | format(row.kwh) }}}}{{{{ ' *' if row.superseded else '' }}}} \
| {{{{ ('{view.currency} ' ~ '{{:,.0f}}'.format(row.rp) | replace(',', '.')) if row.rp else '-' }}}} |
{{%- endfor %}}

{{% if log | selectattr('superseded') | list | count > 0 -%}}
{texts["superseded_note"]}
{{%- endif %}}
{{% else %}}
{texts["no_topup_yet"]}
{{% endif %}}"""

    cards: list[dict[str, Any]] = [{"type": "markdown", "content": template}]
    if rows_entity:
        cards.append(
            {
                "type": "entities",
                "entities": [{"entity": rows_entity, "name": texts["history_rows"]}],
            }
        )
    return cards


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


def _maintenance_cards(
    view: GroupView, texts: dict[str, str]
) -> list[dict[str, Any]]:
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
    return cards


def _mushroom_chips(card: dict[str, Any]) -> dict[str, Any]:
    """Ubah kartu glance jadi baris chip Mushroom yang jauh lebih ringkas."""
    return {
        "type": "custom:mushroom-chips-card",
        "alignment": "center",
        "chips": [
            {
                "type": "entity",
                "entity": row["entity"] if isinstance(row, dict) else row,
                "content_info": "state",
            }
            for row in card.get("entities", [])
        ],
    }


def _apex_chart(card: dict[str, Any]) -> dict[str, Any]:
    """Ubah statistics-graph jadi grafik ApexCharts yang beranimasi."""
    return {
        "type": "custom:apexcharts-card",
        "graph_span": f"{card.get('days_to_show', 30)}d",
        "span": {"end": "day"},
        "header": {"show": True, "title": card.get("title", "")},
        "series": [
            {
                "entity": entity,
                "type": "column",
                "statistics": {"type": "change", "period": "day"},
            }
            for entity in card.get("entities", [])
        ],
    }


def _mushroom_status(view: GroupView, texts: dict[str, str]) -> dict[str, Any] | None:
    """Kartu status token yang ikonnya berubah warna mengikuti keadaan."""
    status = view.entity("token_status")
    remaining = view.entity("token_remaining")
    if not status:
        return None
    color = (
        "{% set s = states('" + status + "') %}"
        "{% if s == 'very_critical' %}red"
        "{% elif s == 'critical' %}deep-orange"
        "{% elif s == 'warning' %}amber"
        "{% elif s == 'normal' %}green"
        "{% else %}grey{% endif %}"
    )
    card: dict[str, Any] = {
        "type": "custom:mushroom-template-card",
        "primary": texts["token_remaining"],
        "icon": "mdi:lightning-bolt-circle",
        "icon_color": color,
        "tap_action": {"action": "more-info"},
        "entity": status,
    }
    if remaining:
        card["secondary"] = "{{ states('" + remaining + "') }} kWh"
    return card


def _to_hacs(
    groups: list[tuple[str, list[dict[str, Any]]]],
    view: GroupView,
    texts: dict[str, str],
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Ganti sebagian kartu bawaan dengan padanan HACS-nya.

    Sengaja hanya sebagian. Kotak isian angka tetap memakai kartu bawaan,
    karena kartu number Mushroom menampilkan penggeser - lebih cantik, tapi
    justru menyulitkan mengetik angka kWh yang persis. Tabel Pemakaian, Biaya,
    dan Riwayat juga tetap markdown, karena tidak ada padanannya.
    """
    out: list[tuple[str, list[dict[str, Any]]]] = []
    for index, (heading, cards) in enumerate(groups):
        new_cards: list[dict[str, Any]] = []
        if index == 0:
            new_cards.append(
                {"type": "markdown", "content": texts["hacs_note"]}
            )
            if status := _mushroom_status(view, texts):
                new_cards.append(status)
        for card in cards:
            if card.get("type") == "glance":
                new_cards.append(_mushroom_chips(card))
            elif card.get("type") == "statistics-graph":
                new_cards.append(_apex_chart(card))
            else:
                new_cards.append(card)
        out.append((heading, new_cards))
    return out


def view_cards(page: dict[str, Any]) -> list[dict[str, Any]]:
    """Seluruh kartu satu halaman, apa pun tata letaknya.

    Tata letak sections menaruh kartu di dalam ``sections``, masonry langsung di
    ``cards``. Fungsi ini menyembunyikan bedanya supaya pemakainya tidak perlu
    tahu tata letak mana yang sedang dipakai.
    """
    if page.get("type") == "sections":
        return [
            card
            for section in page.get("sections", [])
            for card in section.get("cards", [])
        ]
    return list(page.get("cards", []))


def _view_groups(
    view: GroupView, texts: dict[str, str], labels: dict[str, str]
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Kartu halaman, dikelompokkan menurut isinya, masing-masing dengan judul.

    Satu tempat ini saja yang menentukan urutan kartu untuk kedua tata letak:
    pada sections tiap kelompok jadi satu bagian berjudul, pada masonry tiap
    kelompok dibungkus tumpukan supaya kartu yang berkaitan tetap berdampingan.
    """
    groups: list[tuple[str, list[dict[str, Any]]]] = []

    if overview := [*_status_cards(view, texts), *_current_cards(view, texts)]:
        groups.append((texts["sec_overview"], overview))

    usage: list[dict[str, Any]] = []
    if energy_periods := _period_card(view, "energy", texts["usage"], labels):
        usage.append(energy_periods)
    if view.has_cost and (
        cost_periods := _period_card(view, "cost", texts["cost"], labels)
    ):
        usage.append(cost_periods)
    if usage:
        groups.append((texts["sec_usage"], usage))

    token = list(_token_cards(view, texts))
    if view.token_enabled:
        token.extend(_topup_history_cards(view, texts))
    if token:
        groups.append((texts["sec_token"], token))

    if settings := _settings_card(view, texts):
        groups.append((texts["sec_settings"], [settings]))

    if graphs := _history_cards(view, texts):
        groups.append((texts["sec_graphs"], graphs))

    groups.append((texts["sec_maintenance"], _maintenance_cards(view, texts)))
    return groups


def build_view(
    view: GroupView, language: str, layout: str = LAYOUT_SECTIONS
) -> dict[str, Any]:
    """Susun satu halaman dashboard untuk satu kelompok tagihan."""
    texts = SECTION_TITLES[language]
    groups = _view_groups(view, texts, PERIOD_LABELS[language])

    page: dict[str, Any] = {"title": view.name, "path": _slugify(view.name)}

    if layout == LAYOUT_HACS:
        groups = _to_hacs(groups, view, texts)

    if layout in (LAYOUT_SECTIONS, LAYOUT_HACS):
        # Tata letak sections adalah satu-satunya yang mendukung geser kartu
        # dengan drag & drop, dan itu bawaan Home Assistant - bukan fitur kartu
        # pihak ketiga. Lihat docs/decisions.md D-040.
        #
        # Kartu sengaja TIDAK dibungkus vertical-stack di sini: isi tumpukan
        # tidak bisa digeser satu per satu, jadi membungkusnya justru mematikan
        # alasan memilih tata letak ini. Yang mengelompokkan sudah section-nya.
        page["type"] = "sections"
        page["max_columns"] = 3
        page["sections"] = [
            {
                "type": "grid",
                "cards": [
                    {"type": "heading", "heading": heading, "heading_style": "title"},
                    *cards,
                ],
            }
            for heading, cards in groups
        ]
        return page

    # Masonry menyebar kartu ke kolom mana pun yang kosong, jadi di sini justru
    # kebalikannya: tiap kelompok dibungkus supaya tidak terpisah antar kolom.
    page["cards"] = [
        cards[0] if len(cards) == 1 else {"type": "vertical-stack", "cards": cards}
        for _, cards in groups
    ]
    return page


def build_dashboard(
    hass: HomeAssistant, runtime_data: Any, layout: str = LAYOUT_SECTIONS
) -> dict[str, Any]:
    """Susun seluruh dashboard: satu halaman per kelompok tagihan."""
    language = pick_language(hass.config.language)
    views = collect_views(hass, runtime_data)
    return {
        "views": [build_view(view, language, layout) for view in views],
    }
