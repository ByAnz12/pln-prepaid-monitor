"""Layanan (service) untuk mengelola ledger token.

Semua layanan di sini hanya menyentuh catatan token milik integrasi ini.
Tidak ada satu pun yang memanggil perangkat, apalagi memutus listrik.

Cara memilih kelompok tagihan: lewat **target perangkat**. Setiap kelompok
tagihan muncul sebagai satu perangkat di Home Assistant, jadi user tinggal
memilihnya dari daftar - tidak perlu tahu id internal apa pun.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from datetime import timedelta

from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_ACTUAL_REMAINING_KWH,
    ATTR_HOLD_ACTION,
    ATTR_KEEP_YEARS,
    ATTR_KWH_CREDITED,
    ATTR_METER_READING_AFTER,
    ATTR_METER_READING_BEFORE,
    ATTR_NOMINAL_RP,
    ATTR_NOTE,
    ATTR_TIMESTAMP,
    ATTR_APPLY,
    ATTR_LAYOUT,
    ATTR_TOPUP_ID,
    DOMAIN,
    SERVICE_ADD_TOKEN_TOPUP,
    SERVICE_CALIBRATE_TOKEN_READING,
    SERVICE_GENERATE_DASHBOARD,
    SERVICE_DELETE_TOPUP,
    SERVICE_EDIT_TOPUP,
    CONF_RATE_HISTORY,
    CONF_RATE_RP_PER_KWH,
    CONF_STATISTICS_RETENTION_YEARS,
    CONF_TARIFF_ID,
    CONF_TOKEN_PRESETS,
    DEFAULT_STATISTICS_RETENTION_YEARS,
    SERVICE_PURGE_OLD_DATA,
    SERVICE_RESET_TOKEN_LEDGER,
    SERVICE_RESOLVE_LEDGER_HOLD,
    SERVICE_RESOLVE_RATE_CHANGE,
    SERVICE_SAVE_TOPUP_TEMPLATE,
    SERVICE_UPDATE_TOPUP_TEMPLATE,
    SERVICE_DELETE_TOPUP_TEMPLATE,
    SERVICE_TEST_NOTIFICATION,
)
from .engines.token_engine import (
    HOLD_ACTIONS,
    HOLD_ACTION_CALIBRATE,
    find_preset,
    implausible_kwh_hint,
)
from .dashboard import LAYOUT_SECTIONS, LAYOUTS
from .engines.cost_engine import append_rate_version
from .retention import (
    RETENTION_OPTIONS,
    RetentionUnsupportedError,
    async_purge_statistics,
    retention_days,
)

if TYPE_CHECKING:
    from .coordinator import BillingGroupRuntime

_LOGGER = logging.getLogger(__name__)

TARGET_SCHEMA = {vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string])}

ADD_TOPUP_SCHEMA = vol.Schema(
    {
        **TARGET_SCHEMA,
        vol.Optional(ATTR_KWH_CREDITED): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Optional(ATTR_NOMINAL_RP): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Optional(ATTR_TIMESTAMP): cv.datetime,
        vol.Optional(ATTR_METER_READING_BEFORE): vol.Coerce(float),
        vol.Optional(ATTR_METER_READING_AFTER): vol.Coerce(float),
        vol.Optional(ATTR_NOTE): cv.string,
    }
)

CALIBRATE_SCHEMA = vol.Schema(
    {
        **TARGET_SCHEMA,
        vol.Required(ATTR_ACTUAL_REMAINING_KWH): vol.All(
            vol.Coerce(float), vol.Range(min=0)
        ),
        vol.Optional(ATTR_NOTE): cv.string,
    }
)

EDIT_TOPUP_SCHEMA = vol.Schema(
    {
        **TARGET_SCHEMA,
        vol.Required(ATTR_TOPUP_ID): cv.string,
        vol.Optional(ATTR_KWH_CREDITED): vol.All(
            vol.Coerce(float), vol.Range(min=0)
        ),
        vol.Optional(ATTR_NOMINAL_RP): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Optional(ATTR_TIMESTAMP): cv.datetime,
        vol.Optional(ATTR_NOTE): cv.string,
    }
)

DELETE_TOPUP_SCHEMA = vol.Schema(
    {**TARGET_SCHEMA, vol.Required(ATTR_TOPUP_ID): cv.string}
)

RESOLVE_RATE_SCHEMA = vol.Schema(
    {**TARGET_SCHEMA, vol.Required(ATTR_APPLY): cv.boolean}
)

SAVE_TEMPLATE_SCHEMA = vol.Schema({**TARGET_SCHEMA})

RESET_LEDGER_SCHEMA = vol.Schema(
    {**TARGET_SCHEMA, vol.Optional(ATTR_NOTE): cv.string}
)

GENERATE_DASHBOARD_SCHEMA = vol.Schema(
    {vol.Optional(ATTR_LAYOUT): vol.In(LAYOUTS)}
)

PURGE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_KEEP_YEARS): vol.In(RETENTION_OPTIONS),
    }
)

RESOLVE_HOLD_SCHEMA = vol.Schema(
    {
        **TARGET_SCHEMA,
        vol.Required(ATTR_HOLD_ACTION): vol.In(HOLD_ACTIONS),
        vol.Optional(ATTR_ACTUAL_REMAINING_KWH): vol.All(
            vol.Coerce(float), vol.Range(min=0)
        ),
        vol.Optional(ATTR_NOTE): cv.string,
    }
)


def _resolve_groups(
    hass: HomeAssistant, call: ServiceCall
) -> list[BillingGroupRuntime]:
    """Cari kelompok tagihan dari perangkat yang dipilih user."""
    device_registry = dr.async_get(hass)
    groups: list[BillingGroupRuntime] = []

    for device_id in call.data[ATTR_DEVICE_ID]:
        device = device_registry.async_get(device_id)
        if device is None or device.config_subentry_id is None:
            continue
        for entry in hass.config_entries.async_entries(DOMAIN):
            runtime_data = getattr(entry, "runtime_data", None)
            if runtime_data is None:
                continue
            group = runtime_data.billing_groups.get(device.config_subentry_id)
            if group is not None:
                groups.append(group)

    if not groups:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_billing_group_selected"
        )
    return groups


def _require_token_enabled(group: BillingGroupRuntime) -> None:
    """Tolak dengan jelas kalau pencatatan token belum diaktifkan."""
    if not group.token_enabled:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="token_not_enabled",
            translation_placeholders={"group": group.name},
        )


def _resolve_kwh(group: BillingGroupRuntime, call: ServiceCall) -> float:
    """Tentukan berapa kWh yang masuk: dari isian langsung atau dari preset.

    Alur yang paling sering dipakai user: mereka membeli dengan nominal yang
    sama setiap kali, dan struknya selalu menghasilkan kWh yang sama. Jadi cukup
    menyebut nominalnya, dan angka kWh diambil dari preset yang sudah diatur.
    """
    kwh = call.data.get(ATTR_KWH_CREDITED)
    nominal = call.data.get(ATTR_NOMINAL_RP)

    if kwh is None:
        if nominal is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="topup_needs_kwh_or_nominal",
            )
        preset = find_preset(group.token_presets, nominal)
        if preset is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_preset_for_nominal",
                translation_placeholders={"nominal": f"{float(nominal):,.0f}".replace(",", ".")},
            )
        kwh = preset.kwh

    if (hint := implausible_kwh_hint(float(kwh))) is not None:
        # Kasus nyata: struk PLN menulis "82650 KWM" untuk 826,50 kWh.
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="implausible_kwh",
            translation_placeholders={
                "kwh": f"{float(kwh):g}",
                "hint": f"{hint:.2f}".replace(".", ","),
            },
        )

    return float(kwh)


def our_statistic_ids(
    hass: HomeAssistant, subentry_ids: set[str] | None = None
) -> list[str]:
    """entity_id milik integrasi ini saja - pagar utama pembersihan data.

    Inilah yang membuat pembersihan tidak akan pernah menyentuh entity, domain,
    atau data recorder milik user yang lain (spec N.3). Daftarnya dibangun dari
    entity registry, bukan dari pola nama, sehingga tidak ada cara entity asing
    ikut terjaring.
    """
    registry = er.async_get(hass)
    return sorted(
        entry.entity_id
        for entry in registry.entities.values()
        if entry.platform == DOMAIN
        and (subentry_ids is None or entry.config_subentry_id in subentry_ids)
    )


def _resolve_subentry_ids(
    hass: HomeAssistant, call: ServiceCall
) -> set[str] | None:
    """Kelompok tagihan yang dipilih user, atau None untuk semuanya."""
    device_ids = call.data.get(ATTR_DEVICE_ID)
    if not device_ids:
        return None

    device_registry = dr.async_get(hass)
    subentry_ids = {
        device.config_subentry_id
        for device_id in device_ids
        if (device := device_registry.async_get(device_id)) is not None
        and device.config_subentry_id is not None
    }
    if not subentry_ids:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_billing_group_selected"
        )
    return subentry_ids


def _timestamp(call: ServiceCall) -> str:
    """Waktu kejadian: yang diisi user, atau sekarang."""
    given = call.data.get(ATTR_TIMESTAMP)
    if given is None:
        return dt_util.now().isoformat()
    return dt_util.as_local(given).isoformat()


@callback
def async_save_template(hass: HomeAssistant, group: Any) -> None:
    """Simpan isian jumlah kWh dan nominal sekarang sebagai template.

    Di luar ``async_setup_services`` supaya tombol entity dan layanan memakai
    jalur yang persis sama - tidak ada kemungkinan keduanya berperilaku beda.
    """
    _require_token_enabled(group)
    kwh = round(group.inputs.get("topup_kwh", 0.0), 2)
    nominal = group.inputs.get("topup_rp", 0.0)
    if kwh <= 0 or nominal <= 0:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="template_needs_both"
        )

    existing = [preset.as_dict() for preset in group.token_presets]
    if any(
        round(float(item["kwh"]), 2) == kwh
        and float(item.get("nominal_rp") or 0) == nominal
        for item in existing
    ):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="template_already_exists"
        )

    entry = hass.config_entries.async_get_entry(group.entry_id)
    if entry is None:
        return

    name = (group.inputs_text.get("template_name") or "").strip() or None
    if name and any(item.get("name") == name for item in existing):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="template_name_taken"
        )

    # Kosongkan isian namanya SEBELUM menulis konfigurasi: menulis konfigurasi
    # memuat ulang entry, dan runtime yang lama ikut dibuang bersamanya. Kalau
    # dikosongkan sesudahnya, yang dikosongkan adalah objek yang sudah mati -
    # dan namanya diam-diam tertinggal untuk template berikutnya.
    group.async_set_input_text("template_name", "")

    subentry = entry.subentries[group.subentry_id]
    hass.config_entries.async_update_subentry(
        entry,
        subentry,
        data={
            **subentry.data,
            CONF_TOKEN_PRESETS: [
                *existing,
                {"kwh": kwh, "nominal_rp": nominal, "name": name},
            ],
        },
    )
    _LOGGER.info(
        "Template pengisian '%s' disimpan: %s (%s kWh / %s)",
        group.name,
        name or "tanpa nama",
        kwh,
        nominal,
    )


@callback
def _selected_template(group: Any) -> tuple[list[dict[str, Any]], int]:
    """Daftar template dan indeks yang sedang dipilih di dashboard."""
    presets = [preset.as_dict() for preset in group.token_presets]
    chosen = group.inputs_text.get("topup_template")
    labels = [preset.label for preset in group.token_presets]
    if not chosen or chosen not in labels:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_template_selected"
        )
    return presets, labels.index(chosen)


@callback
def _write_templates(
    hass: HomeAssistant, group: Any, presets: list[dict[str, Any]]
) -> None:
    """Tulis daftar template kembali ke konfigurasi kelompok tagihan."""
    entry = hass.config_entries.async_get_entry(group.entry_id)
    if entry is None:
        return
    subentry = entry.subentries[group.subentry_id]
    hass.config_entries.async_update_subentry(
        entry, subentry, data={**subentry.data, CONF_TOKEN_PRESETS: presets}
    )


@callback
def async_update_template(hass: HomeAssistant, group: Any) -> None:
    """Ganti isi template yang sedang dipilih dengan angka di kotak sekarang.

    Inilah cara mengubah template: pilih dulu - kotaknya otomatis terisi -
    perbaiki angkanya, lalu perbarui. Tidak ada layar terpisah yang perlu
    dicari.
    """
    _require_token_enabled(group)
    presets, index = _selected_template(group)

    kwh = round(group.inputs.get("topup_kwh", 0.0), 2)
    nominal = group.inputs.get("topup_rp", 0.0)
    if kwh <= 0 or nominal <= 0:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="template_needs_both"
        )

    name = (group.inputs_text.get("template_name") or "").strip() or None
    if name and any(
        item.get("name") == name for position, item in enumerate(presets)
        if position != index
    ):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="template_name_taken"
        )

    # Nama dikosongkan berarti "pakai nama lama", bukan "hapus namanya":
    # menghapus nama tanpa diminta akan mengejutkan.
    presets[index] = {
        "kwh": kwh,
        "nominal_rp": nominal,
        "name": name or presets[index].get("name"),
    }
    group.async_set_input_text("template_name", "")
    group.async_set_input_text("topup_template", "")
    _write_templates(hass, group, presets)
    _LOGGER.info("Template '%s' diperbarui: %s kWh / %s", group.name, kwh, nominal)


@callback
def async_delete_template(hass: HomeAssistant, group: Any) -> None:
    """Hapus template yang sedang dipilih.

    Tanpa dialog konfirmasi, dan itu disengaja: menghapus template tidak
    menyentuh catatan token sama sekali, dan membuatnya kembali cukup mengetik
    dua angka.
    """
    _require_token_enabled(group)
    presets, index = _selected_template(group)
    removed = presets.pop(index)
    group.async_set_input_text("topup_template", "")
    _write_templates(hass, group, presets)
    _LOGGER.info("Template '%s' dihapus: %s", group.name, removed)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Daftarkan seluruh layanan token, sekali saja."""
    if hass.services.has_service(DOMAIN, SERVICE_ADD_TOKEN_TOPUP):
        return

    async def async_add_topup(call: ServiceCall) -> None:
        """Catat pengisian token baru."""
        for group in _resolve_groups(hass, call):
            _require_token_enabled(group)
            group.record_topup(
                kwh_credited=_resolve_kwh(group, call),
                timestamp=_timestamp(call),
                nominal_rp=call.data.get(ATTR_NOMINAL_RP),
                meter_reading_before=call.data.get(ATTR_METER_READING_BEFORE),
                meter_reading_after=call.data.get(ATTR_METER_READING_AFTER),
                note=call.data.get(ATTR_NOTE),
            )

    async def async_calibrate(call: ServiceCall) -> None:
        """Samakan ledger dengan angka di layar meteran fisik."""
        for group in _resolve_groups(hass, call):
            _require_token_enabled(group)
            group.calibrate_to(
                actual_remaining_kwh=call.data[ATTR_ACTUAL_REMAINING_KWH],
                timestamp=_timestamp(call),
                note=call.data.get(ATTR_NOTE),
            )

    async def async_edit_topup(call: ServiceCall) -> None:
        """Perbaiki entri top-up yang salah input."""
        for group in _resolve_groups(hass, call):
            _require_token_enabled(group)
            changes: dict[str, Any] = {
                "kwh_credited": call.data.get(ATTR_KWH_CREDITED),
                "nominal_rp": call.data.get(ATTR_NOMINAL_RP),
                "note": call.data.get(ATTR_NOTE),
            }
            if ATTR_TIMESTAMP in call.data:
                changes["timestamp"] = _timestamp(call)
            updated = group.ledger.edit_topup(call.data[ATTR_TOPUP_ID], **changes)
            if updated is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="topup_not_found",
                    translation_placeholders={"topup_id": call.data[ATTR_TOPUP_ID]},
                )
            group.async_ledger_changed()

    async def async_delete_topup(call: ServiceCall) -> None:
        """Hapus entri top-up yang tidak seharusnya ada."""
        for group in _resolve_groups(hass, call):
            _require_token_enabled(group)
            if not group.ledger.delete_topup(call.data[ATTR_TOPUP_ID]):
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="topup_not_found",
                    translation_placeholders={"topup_id": call.data[ATTR_TOPUP_ID]},
                )
            group.async_ledger_changed()

    async def async_reset_ledger(call: ServiceCall) -> None:
        """Mulai pencatatan token dari nol."""
        for group in _resolve_groups(hass, call):
            _require_token_enabled(group)
            group.ledger.reset(
                group_total=group.total_kwh,
                timestamp=_timestamp(call),
                note=call.data.get(ATTR_NOTE),
            )
            _LOGGER.warning(
                "Ledger token '%s' di-reset penuh atas permintaan user", group.name
            )
            group.async_ledger_changed()

    async def async_resolve_hold(call: ServiceCall) -> None:
        """Lepaskan penahanan ledger sesuai keputusan user."""
        action = call.data[ATTR_HOLD_ACTION]
        for group in _resolve_groups(hass, call):
            _require_token_enabled(group)
            if not group.ledger.on_hold:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="ledger_not_on_hold",
                    translation_placeholders={"group": group.name},
                )
            if (
                action == HOLD_ACTION_CALIBRATE
                and call.data.get(ATTR_ACTUAL_REMAINING_KWH) is None
            ):
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="calibration_needs_reading",
                )
            group.ledger.resolve_hold(
                action=action,
                group_total=group.total_kwh,
                timestamp=_timestamp(call),
                actual_remaining_kwh=call.data.get(ATTR_ACTUAL_REMAINING_KWH),
            )
            _LOGGER.info(
                "Penahanan ledger '%s' dilepas dengan keputusan '%s'",
                group.name,
                action,
            )
            group.async_ledger_changed()

    async def async_resolve_rate_change(call: ServiceCall) -> None:
        """Terima atau tolak usulan harga efektif per kWh."""
        apply = bool(call.data[ATTR_APPLY])
        for group in _resolve_groups(hass, call):
            if group.pending_rate is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="no_rate_proposal",
                    translation_placeholders={"group": group.name},
                )
            proposal = group.resolve_rate_change(apply=apply)
            if proposal is None:
                _LOGGER.info("Usulan harga '%s' ditolak user", group.name)
                continue

            entry = hass.config_entries.async_get_entry(group.entry_id)
            tariff_id = group.config.get(CONF_TARIFF_ID)
            if entry is None or not tariff_id or tariff_id not in entry.subentries:
                continue
            subentry = entry.subentries[tariff_id]
            rate = float(proposal["to_rate"])
            hass.config_entries.async_update_subentry(
                entry,
                subentry,
                data={
                    **subentry.data,
                    CONF_RATE_RP_PER_KWH: rate,
                    # Versi baru, tidak pernah menimpa yang lama: biaya yang
                    # sudah tercatat tetap memakai harga saat pemakaian itu
                    # terjadi (spec K.7).
                    CONF_RATE_HISTORY: append_rate_version(
                        list(subentry.data.get(CONF_RATE_HISTORY) or []),
                        rate,
                        dt_util.now().isoformat(),
                    ),
                },
            )
            _LOGGER.info("Harga '%s' diperbarui jadi %s/kWh", group.name, rate)

    async def async_save_topup_template(call: ServiceCall) -> None:
        """Simpan isian jumlah kWh dan nominal sekarang sebagai template."""
        for group in _resolve_groups(hass, call):
            async_save_template(hass, group)

    async def async_update_topup_template(call: ServiceCall) -> None:
        """Perbarui template yang sedang dipilih."""
        for group in _resolve_groups(hass, call):
            async_update_template(hass, group)

    async def async_delete_topup_template(call: ServiceCall) -> None:
        """Hapus template yang sedang dipilih."""
        for group in _resolve_groups(hass, call):
            async_delete_template(hass, group)

    async def async_test_notification(call: ServiceCall) -> None:
        """Kirim satu pesan percobaan lewat tujuan notifikasi yang diatur."""
        from .notifier import TokenNotifier  # noqa: PLC0415

        for group in _resolve_groups(hass, call):
            await TokenNotifier(hass, group).async_send_test()

    async def async_generate_dashboard(call: ServiceCall) -> ServiceResponse:
        """Susun konfigurasi dashboard Lovelace untuk kelompok tagihan yang ada.

        Hanya membaca dan mengembalikan konfigurasi - tidak menulis file, tidak
        mengubah dashboard yang sudah ada. User yang menempelkannya sendiri.
        """
        from .dashboard import build_dashboard  # noqa: PLC0415

        entries = hass.config_entries.async_entries(DOMAIN)
        runtime_data = next(
            (
                entry.runtime_data
                for entry in entries
                if getattr(entry, "runtime_data", None) is not None
            ),
            None,
        )
        if runtime_data is None or not runtime_data.billing_groups:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_billing_groups"
            )

        # Responsnya sengaja berupa konfigurasi dashboard itu sendiri, tanpa
        # satu pun kunci tambahan. Developer Tools menampilkan response sebagai
        # YAML dan user menyalinnya bulat-bulat; kunci tambahan apa pun (dulu
        # "yaml" dan jumlah "views") membuat Raw configuration editor menolak
        # hasil tempelan itu. Lihat docs/decisions.md D-036.
        return build_dashboard(
            hass, runtime_data, call.data.get(ATTR_LAYOUT, LAYOUT_SECTIONS)
        )

    async def async_purge_old_data(call: ServiceCall) -> ServiceResponse:
        """Hapus statistik lama milik integrasi ini saja."""
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_billing_groups"
            )

        keep_years = call.data.get(ATTR_KEEP_YEARS) or entries[0].options.get(
            CONF_STATISTICS_RETENTION_YEARS, DEFAULT_STATISTICS_RETENTION_YEARS
        )
        days = retention_days(keep_years)
        if days is None:
            # "Simpan selamanya" berarti benar-benar tidak menghapus apa pun -
            # bukan diam-diam memakai angka bawaan.
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="retention_unlimited"
            )

        subentry_ids = _resolve_subentry_ids(hass, call)
        statistic_ids = our_statistic_ids(hass, subentry_ids)
        cutoff = dt_util.utcnow() - timedelta(days=days)

        try:
            result = await async_purge_statistics(hass, statistic_ids, cutoff)
        except RetentionUnsupportedError as err:
            # Gagal terang-terangan, bukan diam-diam salah hapus (spec N.4).
            _LOGGER.error("Pembersihan data tidak bisa dijalankan: %s", err)
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="retention_unsupported",
                translation_placeholders={"reason": str(err)},
            ) from err

        return result.as_response()

    hass.services.async_register(
        DOMAIN, SERVICE_ADD_TOKEN_TOPUP, async_add_topup, schema=ADD_TOPUP_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CALIBRATE_TOKEN_READING,
        async_calibrate,
        schema=CALIBRATE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_EDIT_TOPUP, async_edit_topup, schema=EDIT_TOPUP_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DELETE_TOPUP, async_delete_topup, schema=DELETE_TOPUP_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_TOKEN_LEDGER,
        async_reset_ledger,
        schema=RESET_LEDGER_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESOLVE_RATE_CHANGE,
        async_resolve_rate_change,
        schema=RESOLVE_RATE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SAVE_TOPUP_TEMPLATE,
        async_save_topup_template,
        schema=SAVE_TEMPLATE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_TOPUP_TEMPLATE,
        async_update_topup_template,
        schema=SAVE_TEMPLATE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_TOPUP_TEMPLATE,
        async_delete_topup_template,
        schema=SAVE_TEMPLATE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_TEST_NOTIFICATION,
        async_test_notification,
        schema=SAVE_TEMPLATE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESOLVE_LEDGER_HOLD,
        async_resolve_hold,
        schema=RESOLVE_HOLD_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GENERATE_DASHBOARD,
        async_generate_dashboard,
        schema=GENERATE_DASHBOARD_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_PURGE_OLD_DATA,
        async_purge_old_data,
        schema=PURGE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


@callback
def async_unload_services(hass: HomeAssistant) -> None:
    """Lepaskan layanan saat integrasi dicopot."""
    for service in (
        SERVICE_ADD_TOKEN_TOPUP,
        SERVICE_CALIBRATE_TOKEN_READING,
        SERVICE_EDIT_TOPUP,
        SERVICE_DELETE_TOPUP,
        SERVICE_RESET_TOKEN_LEDGER,
        SERVICE_RESOLVE_LEDGER_HOLD,
        SERVICE_GENERATE_DASHBOARD,
        SERVICE_PURGE_OLD_DATA,
    ):
        hass.services.async_remove(DOMAIN, service)
