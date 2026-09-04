"""Teks laporan hasil pemeriksaan sumber, dalam Bahasa Indonesia dan Inggris.

Kenapa tidak lewat ``translations/*.json`` biasa: halaman "Cek dulu sebelum
disimpan" bukan label statis, melainkan *laporan yang dirakit saat itu juga*
dari hasil pemeriksaan entity. Teksnya perlu digabung jadi satu blok markdown
sebelum diserahkan ke form, jadi lebih jujur menaruhnya di sini sebagai
katalog kalimat daripada memaksakannya ke sistem label form.

Label form dan deskripsi field yang statis tetap memakai
``translations/*.json`` sebagaimana mestinya.
"""

from __future__ import annotations

from typing import Any

from .const import (
    CHANNEL_CURRENT,
    CHANNEL_ENERGY,
    CHANNEL_FREQUENCY,
    CHANNEL_POWER,
    CHANNEL_VOLTAGE,
)

LANG_ID = "id"
LANG_EN = "en"

ROLE_LABELS: dict[str, dict[str, str]] = {
    LANG_ID: {
        CHANNEL_ENERGY: "Energi (kWh)",
        CHANNEL_POWER: "Daya (W)",
        CHANNEL_VOLTAGE: "Tegangan (V)",
        CHANNEL_CURRENT: "Arus (A)",
        CHANNEL_FREQUENCY: "Frekuensi (Hz)",
    },
    LANG_EN: {
        CHANNEL_ENERGY: "Energy (kWh)",
        CHANNEL_POWER: "Power (W)",
        CHANNEL_VOLTAGE: "Voltage (V)",
        CHANNEL_CURRENT: "Current (A)",
        CHANNEL_FREQUENCY: "Frequency (Hz)",
    },
}

ISSUE_TEXTS: dict[str, dict[str, str]] = {
    LANG_ID: {
        "entity_not_found": (
            "Sensor {entity_id} belum ada di Home Assistant saat ini. Boleh "
            "disimpan; sistem akan memakainya begitu sensornya muncul."
        ),
        "entity_unavailable": (
            "Sensor {entity_id} sedang tidak tersedia. Boleh disimpan; sistem "
            "akan menunggu sampai sensornya hidup kembali."
        ),
        "state_not_numeric": (
            "Sensor {entity_id} berisi '{state}', bukan angka. Pilih sensor lain "
            "yang isinya angka."
        ),
        "unit_converted": (
            "Satuan sensor ini {unit}, dikonversi otomatis ke {target_unit} "
            "(dikali {factor})."
        ),
        "unit_not_convertible": (
            "Satuan {unit} tidak bisa dikonversi ke {target_unit}. Pastikan Anda "
            "memilih sensor dengan jenis pengukuran yang benar."
        ),
        "no_unit": (
            "Sensor ini tidak menyebutkan satuannya. Sistem akan menganggapnya "
            "sudah dalam {target_unit} - mohon pastikan itu benar."
        ),
        "device_class_mismatch": (
            "Home Assistant menandai sensor ini sebagai '{device_class}', "
            "sedangkan yang diharapkan '{expected}'. Sistem tetap memakainya, "
            "tapi periksa lagi apakah Anda memilih sensor yang tepat."
        ),
        "energy_no_state_class": (
            "Sensor ini tidak memberi tahu Home Assistant bahwa angkanya selalu "
            "naik. Sistem akan memperlakukannya sebagai penghitung yang selalu "
            "naik, lengkap dengan pengaman kalau angkanya tiba-tiba direset."
        ),
        "energy_state_class_measurement": (
            "Sensor ini ditandai sebagai pengukuran sesaat, bukan penghitung "
            "kumulatif. Kalau angkanya naik-turun (bukan terus bertambah), "
            "hasil perhitungan akan salah - pilih sensor kWh yang terus bertambah."
        ),
        "energy_from_power": (
            "Tidak ada sensor kWh yang dipilih. Sistem akan memperkirakan kWh "
            "dengan menjumlahkan daya dari waktu ke waktu. Hasilnya perkiraan "
            "dan kurang akurat dibanding sensor kWh asli."
        ),
        "no_measurement_entity": (
            "Isi minimal salah satu: sensor Energi (kWh) atau sensor Daya (W). "
            "Tanpa salah satunya, tidak ada yang bisa dihitung."
        ),
        "name_required": "Nama sumber tidak boleh kosong.",
        "name_duplicate": (
            "Sudah ada sumber energi bernama '{name}'. Pakai nama lain supaya "
            "tidak tertukar."
        ),
        "own_entity_selected": (
            "Sensor {entity_id} adalah buatan integrasi ini sendiri. Memakainya "
            "sebagai sumber akan membuat perhitungan berputar-putar - pilih "
            "sensor asli dari perangkat Anda."
        ),
        "no_sources_selected": (
            "Pilih minimal satu sumber energi untuk kelompok tagihan ini."
        ),
        "no_periods_selected": (
            "Pilih minimal satu periode. Tanpa itu tidak ada penghitung pemakaian "
            "yang dibuat."
        ),
        "source_used_by_other_group": (
            "Sumber '{source}' juga dipakai oleh kelompok '{group}'. Pemakaiannya "
            "akan terhitung di kedua kelompok. Ini boleh saja kalau memang "
            "disengaja (misalnya untuk dua sudut pandang berbeda), tapi jangan "
            "menjumlahkan angka kedua kelompok itu."
        ),
    },
    LANG_EN: {
        "entity_not_found": (
            "Sensor {entity_id} does not exist in Home Assistant right now. You "
            "can still save; it will be picked up once the sensor appears."
        ),
        "entity_unavailable": (
            "Sensor {entity_id} is currently unavailable. You can still save; "
            "the system will wait until it comes back."
        ),
        "state_not_numeric": (
            "Sensor {entity_id} reads '{state}', which is not a number. Pick a "
            "sensor that reports a number."
        ),
        "unit_converted": (
            "This sensor reports {unit}; it will be converted to {target_unit} "
            "(multiplied by {factor})."
        ),
        "unit_not_convertible": (
            "Unit {unit} cannot be converted to {target_unit}. Check that you "
            "picked a sensor of the right measurement type."
        ),
        "no_unit": (
            "This sensor does not state its unit. It will be assumed to already "
            "be in {target_unit} - please confirm that is correct."
        ),
        "device_class_mismatch": (
            "Home Assistant labels this sensor as '{device_class}' while "
            "'{expected}' was expected. It will still be used, but double-check "
            "you picked the right sensor."
        ),
        "energy_no_state_class": (
            "This sensor does not tell Home Assistant that its value only ever "
            "increases. It will be treated as an ever-increasing counter, with "
            "protection in case the number is reset."
        ),
        "energy_state_class_measurement": (
            "This sensor is marked as an instantaneous measurement rather than a "
            "cumulative counter. If its value goes up and down, the results will "
            "be wrong - pick a kWh sensor that keeps increasing."
        ),
        "energy_from_power": (
            "No kWh sensor selected. kWh will be estimated by adding up power "
            "over time. That is an estimate and less accurate than a real kWh "
            "sensor."
        ),
        "no_measurement_entity": (
            "Fill in at least one of: Energy (kWh) sensor or Power (W) sensor. "
            "Without either there is nothing to calculate."
        ),
        "name_required": "The source name cannot be empty.",
        "name_duplicate": (
            "An energy source named '{name}' already exists. Use a different "
            "name to avoid confusion."
        ),
        "own_entity_selected": (
            "Sensor {entity_id} is created by this integration itself. Using it "
            "as a source would make the calculation feed on itself - pick a real "
            "sensor from your device."
        ),
        "no_sources_selected": (
            "Pick at least one energy source for this billing group."
        ),
        "no_periods_selected": (
            "Pick at least one period. Without one, no usage counter is created."
        ),
        "source_used_by_other_group": (
            "Source '{source}' is also used by group '{group}'. Its usage will be "
            "counted in both groups. That is fine if it is deliberate (for "
            "example two different viewpoints), but do not add the two groups' "
            "numbers together."
        ),
    },
}

REPORT_TEXTS: dict[str, dict[str, str]] = {
    LANG_ID: {
        "not_configured": "tidak dipakai",
        "ok": "OK",
        "source_of_truth_cumulative": (
            "**Patokan perhitungan**: pembacaan kWh kumulatif (paling akurat)."
        ),
        "source_of_truth_integrated": (
            "**Patokan perhitungan**: perkiraan dari daya (kurang akurat)."
        ),
        "no_issues": "Tidak ada yang perlu diperhatikan.",
        "errors_header": "Harus diperbaiki dulu",
        "reading_now": "terbaca sekarang",
        "members_header": "Sumber energi yang digabung",
        "total_now": "Total gabungan saat ini",
        "periods_header": "Penghitung yang akan dibuat",
        "resets_at": "reset berikutnya",
        "no_data_yet": "belum ada data",
        "tariff_header": "Tarif listrik",
        "no_tariff": "belum dipilih - biaya belum akan dihitung",
    },
    LANG_EN: {
        "not_configured": "not used",
        "ok": "OK",
        "source_of_truth_cumulative": (
            "**Basis for calculation**: cumulative kWh reading (most accurate)."
        ),
        "source_of_truth_integrated": (
            "**Basis for calculation**: estimated from power (less accurate)."
        ),
        "no_issues": "Nothing needs your attention.",
        "errors_header": "Must be fixed first",
        "reading_now": "currently reads",
        "members_header": "Energy sources combined",
        "total_now": "Combined total right now",
        "periods_header": "Counters that will be created",
        "resets_at": "next reset",
        "no_data_yet": "no data yet",
        "tariff_header": "Electricity tariff",
        "no_tariff": "not selected - cost will not be calculated yet",
    },
}

PERIOD_LABELS: dict[str, dict[str, str]] = {
    LANG_ID: {
        "hour": "Jam ini",
        "day": "Hari ini",
        "week": "Minggu ini",
        "month": "Bulan ini",
        "year": "Tahun ini",
        "avg_hourly": "Rata-rata per jam",
        "avg_daily": "Rata-rata harian",
        "avg_monthly": "Rata-rata bulanan",
        "this_hour": "Jam ini",
        "this_day": "Hari ini",
        "prev_day_1": "Hari kemarin",
        "prev_day_2": "2 hari lalu",
        "prev_day_3": "3 hari lalu",
        "this_week": "Minggu ini",
        "this_month": "Bulan ini",
        "prev_month_1": "Bulan kemarin",
        "prev_month_2": "2 bulan lalu",
        "prev_month_3": "3 bulan lalu",
        "this_year": "Tahun ini",
    },
    LANG_EN: {
        "hour": "This hour",
        "day": "Today",
        "week": "This week",
        "month": "This month",
        "year": "This year",
        "avg_hourly": "Hourly average",
        "avg_daily": "Daily average",
        "avg_monthly": "Monthly average",
        "this_hour": "This hour",
        "this_day": "Today",
        "prev_day_1": "Yesterday",
        "prev_day_2": "2 days ago",
        "prev_day_3": "3 days ago",
        "this_week": "This week",
        "this_month": "This month",
        "prev_month_1": "Last month",
        "prev_month_2": "2 months ago",
        "prev_month_3": "3 months ago",
        "this_year": "This year",
    },
}


def pick_language(language: str | None) -> str:
    """Pilih katalog bahasa: Indonesia bila HA berbahasa Indonesia."""
    if language and language.lower().startswith("id"):
        return LANG_ID
    return LANG_EN


def issue_text(language: str, code: str, placeholders: dict[str, str]) -> str:
    """Rakit satu kalimat temuan."""
    catalog = ISSUE_TEXTS[pick_language(language)]
    template = catalog.get(code)
    if template is None:
        return code
    try:
        return template.format(**placeholders)
    except KeyError:
        return template


# --- teks notifikasi ---------------------------------------------------------

STATUS_LABELS: dict[str, dict[str, str]] = {
    LANG_ID: {
        "warning": "perlu perhatian",
        "critical": "kritis",
        "very_critical": "SANGAT KRITIS",
        "hold": "ditahan",
        "normal": "aman",
    },
    LANG_EN: {
        "warning": "needs attention",
        "critical": "critical",
        "very_critical": "VERY CRITICAL",
        "hold": "on hold",
        "normal": "normal",
    },
}

NOTIFICATION_TEXTS: dict[str, dict[str, str]] = {
    LANG_ID: {
        "alert_title": "{prefix} {group}: token {status}",
        "alert_body": "Sisa token {group} tinggal {remaining}.",
        "recovery_title": "{prefix} {group}: token sudah terisi",
        "recovery_body": "Token {group} sudah terisi lagi. Sisa sekarang {remaining}.",
        "hold_title": "{prefix} {group}: pencatatan token ditahan",
        "hold_body": (
            "Pencatatan sisa token {group} dibekukan karena meteran '{source}' "
            "melompat ke {reset_to} kWh sesudah counter-nya ter-reset.\n\n"
            "Sisa token ditahan di angka terakhir supaya tidak hangus salah. "
            "Silakan putuskan lewat layanan 'Putuskan penahanan token': anggap "
            "pemakaian nyata, abaikan, atau kalibrasi dari angka di layar meteran."
        ),
        "days_line": "Perkiraan habis {days} hari lagi, sekitar {date}.",
        "no_prediction_line": (
            "Perkiraan hari tersisa belum tersedia - data pemakaian belum cukup."
        ),
        "value_suffix": " (setara sekitar Rp {value})",
        "unknown_remaining": "belum diketahui",
    },
    LANG_EN: {
        "alert_title": "{prefix} {group}: token {status}",
        "alert_body": "Only {remaining} of token left for {group}.",
        "recovery_title": "{prefix} {group}: token topped up",
        "recovery_body": "{group} has been topped up. {remaining} remaining.",
        "hold_title": "{prefix} {group}: token tracking on hold",
        "hold_body": (
            "Token tracking for {group} is frozen because meter '{source}' "
            "jumped to {reset_to} kWh after its counter reset.\n\n"
            "The remaining token is held at its last value so it is not written "
            "off by mistake. Please decide via the 'Decide on a token hold' "
            "action: treat as real usage, ignore, or calibrate from the meter."
        ),
        "days_line": "Estimated to run out in {days} days, around {date}.",
        "no_prediction_line": (
            "No day estimate yet - there is not enough usage data."
        ),
        "value_suffix": " (worth roughly Rp {value})",
        "unknown_remaining": "unknown",
    },
}


def _format_number(value: float, decimals: int = 2) -> str:
    """Angka bergaya Indonesia: titik ribuan, koma desimal."""
    formatted = f"{value:,.{decimals}f}"
    return formatted.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def notification_text(
    language: str,
    *,
    kind: str,
    status: str,
    prefix: str,
    group_name: str,
    remaining_kwh: float | None,
    days_remaining: float | None,
    empty_date: Any,
    remaining_value_rp: float | None,
    hold: dict[str, Any] | None,
) -> tuple[str, str]:
    """Rakit judul dan isi satu notifikasi token."""
    texts = NOTIFICATION_TEXTS[language]
    labels = STATUS_LABELS[language]

    if kind == "hold":
        hold = hold or {}
        title = texts["hold_title"].format(prefix=prefix, group=group_name)
        body = texts["hold_body"].format(
            group=group_name,
            source=hold.get("source_name", "?"),
            reset_to=_format_number(float(hold.get("reset_to") or 0)),
        )
        return title, body

    if remaining_kwh is None:
        remaining = texts["unknown_remaining"]
    else:
        remaining = f"{_format_number(remaining_kwh)} kWh"
        if remaining_value_rp is not None:
            remaining += texts["value_suffix"].format(
                value=_format_number(remaining_value_rp, 0)
            )

    if kind == "recovery":
        title = texts["recovery_title"].format(prefix=prefix, group=group_name)
        body = texts["recovery_body"].format(group=group_name, remaining=remaining)
        return title, body

    title = texts["alert_title"].format(
        prefix=prefix, group=group_name, status=labels.get(status, status)
    )
    lines = [texts["alert_body"].format(group=group_name, remaining=remaining)]

    if days_remaining is None or empty_date is None:
        lines.append(texts["no_prediction_line"])
    else:
        lines.append(
            texts["days_line"].format(
                days=_format_number(days_remaining, 1),
                date=empty_date.strftime("%d %b %Y %H:%M"),
            )
        )

    return title, "\n".join(lines)


# Simbol yang dipakai Home Assistant sendiri untuk mata uang yang sering dipakai
# di sekitar Indonesia. Kartu markdown tidak bisa memanggil pemformat mata uang
# milik frontend, jadi supaya angka di kartu Biaya tampil sama seperti di sensor
# biaya (yang diformat frontend), simbolnya diambil dari sini.
#
# Ini bukan daftar lengkap dan tidak perlu lengkap: mata uang yang tidak ada di
# sini ditampilkan memakai kode ISO-nya, yang tetap jelas dan tidak pernah salah.
CURRENCY_SYMBOLS: dict[str, str] = {
    "IDR": "Rp",
    "MYR": "RM",
    "SGD": "S$",
    "PHP": "₱",
    "THB": "฿",
    "VND": "₫",
    "INR": "₹",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "AUD": "A$",
}


def currency_symbol(code: str | None) -> str:
    """Simbol mata uang, atau kode ISO-nya kalau simbolnya tidak dikenal."""
    if not code:
        return ""
    return CURRENCY_SYMBOLS.get(code.upper(), code.upper())
