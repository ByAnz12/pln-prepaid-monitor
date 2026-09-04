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
    ATTR_TOPUP_ID,
    DOMAIN,
    SERVICE_ADD_TOKEN_TOPUP,
    SERVICE_CALIBRATE_TOKEN_READING,
    SERVICE_GENERATE_DASHBOARD,
    SERVICE_DELETE_TOPUP,
    SERVICE_EDIT_TOPUP,
    CONF_STATISTICS_RETENTION_YEARS,
    DEFAULT_STATISTICS_RETENTION_YEARS,
    SERVICE_PURGE_OLD_DATA,
    SERVICE_RESET_TOKEN_LEDGER,
    SERVICE_RESOLVE_LEDGER_HOLD,
)
from .engines.token_engine import (
    HOLD_ACTIONS,
    HOLD_ACTION_CALIBRATE,
    find_preset,
    implausible_kwh_hint,
)
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

RESET_LEDGER_SCHEMA = vol.Schema(
    {**TARGET_SCHEMA, vol.Optional(ATTR_NOTE): cv.string}
)

GENERATE_DASHBOARD_SCHEMA = vol.Schema({})

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
        return build_dashboard(hass, runtime_data)

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
