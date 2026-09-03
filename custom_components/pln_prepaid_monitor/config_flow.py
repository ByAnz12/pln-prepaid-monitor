"""Config flow dan subentry flow untuk PLN Prepaid Energy & Cost Monitor.

Susunan yang dilihat user:

* **Setup pertama kali** - halaman penjelasan, lalu langsung diajak menambah
  Energy Source pertama (boleh dilewati).
* **Tambah Energy Source** - 3 langkah:
  1. ``pick_device``  : pilih perangkat (opsional, hanya untuk menebak isian)
  2. ``map_entities`` : petakan sensor ke peran kanonik
  3. ``review``       : lihat hasil pemeriksaan sebelum benar-benar disimpan
* **Edit Energy Source** - langsung ke ``map_entities`` lalu ``review``.

Catatan keamanan yang disengaja: seluruh pemilih entity dibatasi ke domain
``sensor`` (dan ``binary_sensor`` untuk availability). Domain ``switch``,
``number``, dan ``select`` tidak pernah muncul sebagai pilihan, sehingga
entity relay/breaker milik MCB secara struktural mustahil terpilih - bukan
sekadar disaring per entity_id yang bisa bocor untuk perangkat baru.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryData,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    DeviceSelector,
    DeviceSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TimeSelector,
)
from homeassistant.util import dt as dt_util

from .const import (
    ALLOWED_AVAILABILITY_DOMAINS,
    ALLOWED_SOURCE_DOMAINS,
    CHANNEL_CONF_KEYS,
    CHANNEL_CURRENT,
    CHANNEL_ENERGY,
    CHANNEL_FREQUENCY,
    CHANNEL_POWER,
    CHANNEL_VOLTAGE,
    CONF_AVAILABILITY_ENTITY_ID,
    CONF_CYCLE_PERIODS,
    CONF_DAY_START_TIME,
    CONF_DEVICE_ID,
    CONF_ENABLED,
    CONF_MONTH_START_DAY,
    CONF_SHOW_ALL_SENSORS,
    CONF_SOURCE_IDS,
    CONF_UNAVAILABLE_GRACE_MINUTES,
    CONF_WEEK_START_DAY,
    CONF_YEAR_START_MONTH,
    DEFAULT_CYCLE_PERIODS,
    DEFAULT_DAY_START_TIME,
    DEFAULT_MONTH_START_DAY,
    DEFAULT_UNAVAILABLE_GRACE_MINUTES,
    DEFAULT_WEEK_START_DAY,
    DEFAULT_YEAR_START_MONTH,
    DOMAIN,
    SUBENTRY_TYPE_BILLING_GROUP,
    SUBENTRY_TYPE_ENERGY_SOURCE,
)
from .engines.normalization import (
    CHANNEL_SPECS,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    Issue,
    SourceReport,
    inspect_source,
)
from .engines.period import (
    ALL_PERIODS,
    MAX_MONTH_START_DAY,
    MONTHS,
    WEEKDAYS,
    CycleConfig,
    next_cycle_start,
)
from .messages import (
    PERIOD_LABELS,
    REPORT_TEXTS,
    ROLE_LABELS,
    issue_text,
    pick_language,
)

CONF_ADD_SOURCE_NOW = "add_source_now"

SEVERITY_MARKERS = {
    SEVERITY_ERROR: "⛔",
    SEVERITY_WARNING: "⚠️",
    SEVERITY_INFO: "ℹ️",
}

# Urutan tampil di form dan di laporan.
CHANNEL_ORDER = (
    CHANNEL_ENERGY,
    CHANNEL_POWER,
    CHANNEL_VOLTAGE,
    CHANNEL_CURRENT,
    CHANNEL_FREQUENCY,
)


def _own_entity_ids(hass: HomeAssistant) -> list[str]:
    """Entity buatan integrasi ini sendiri, supaya tidak bisa dipilih ulang."""
    registry = er.async_get(hass)
    return [
        entry.entity_id
        for entry in registry.entities.values()
        if entry.platform == DOMAIN
    ]


def _entity_selector(
    hass: HomeAssistant, role: str | None, show_all: bool
) -> EntitySelector:
    """Pemilih entity yang dibatasi ke domain aman dan (opsional) device_class."""
    if role is None:
        filters: list[dict[str, Any]] = [
            {"domain": list(ALLOWED_AVAILABILITY_DOMAINS)}
        ]
    else:
        entity_filter: dict[str, Any] = {"domain": list(ALLOWED_SOURCE_DOMAINS)}
        if not show_all:
            entity_filter["device_class"] = CHANNEL_SPECS[role].device_class
        filters = [entity_filter]
    return EntitySelector(
        EntitySelectorConfig(filter=filters, exclude_entities=_own_entity_ids(hass))
    )


def _pick_device_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Skema langkah 1: pilih perangkat (opsional)."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_DEVICE_ID,
                description={"suggested_value": defaults.get(CONF_DEVICE_ID)},
            ): DeviceSelector(
                DeviceSelectorConfig(
                    entity=[
                        {"domain": "sensor", "device_class": "energy"},
                        {"domain": "sensor", "device_class": "power"},
                    ]
                )
            ),
            vol.Required(
                CONF_SHOW_ALL_SENSORS,
                default=bool(defaults.get(CONF_SHOW_ALL_SENSORS, False)),
            ): BooleanSelector(),
        }
    )


def _map_entities_schema(
    hass: HomeAssistant, defaults: dict[str, Any], show_all: bool
) -> vol.Schema:
    """Skema langkah 2: pemetaan sensor ke peran kanonik."""
    schema: dict[Any, Any] = {
        vol.Required(
            CONF_NAME, description={"suggested_value": defaults.get(CONF_NAME)}
        ): TextSelector()
    }
    for role in CHANNEL_ORDER:
        conf_key = CHANNEL_CONF_KEYS[role]
        schema[
            vol.Optional(
                conf_key, description={"suggested_value": defaults.get(conf_key)}
            )
        ] = _entity_selector(hass, role, show_all)

    schema[
        vol.Optional(
            CONF_AVAILABILITY_ENTITY_ID,
            description={
                "suggested_value": defaults.get(CONF_AVAILABILITY_ENTITY_ID)
            },
        )
    ] = _entity_selector(hass, None, show_all)

    schema[
        vol.Required(
            CONF_UNAVAILABLE_GRACE_MINUTES,
            default=float(
                defaults.get(
                    CONF_UNAVAILABLE_GRACE_MINUTES, DEFAULT_UNAVAILABLE_GRACE_MINUTES
                )
            ),
        )
    ] = NumberSelector(
        NumberSelectorConfig(min=0, max=1440, step=1, mode=NumberSelectorMode.BOX)
    )
    schema[
        vol.Required(CONF_ENABLED, default=bool(defaults.get(CONF_ENABLED, True)))
    ] = BooleanSelector()
    return vol.Schema(schema)


def _suggest_from_device(hass: HomeAssistant, device_id: str) -> dict[str, Any]:
    """Tebak pemetaan sensor dari sebuah perangkat.

    Murni struktural: hanya melihat ``device_class`` dan ``state_class``, tidak
    ada aturan khusus merek atau model apa pun. Hasil tebakan selalu ditampilkan
    ke user di langkah berikutnya untuk dikoreksi.
    """
    registry = er.async_get(hass)
    entries = er.async_entries_for_device(
        registry, device_id, include_disabled_entities=False
    )
    by_role: dict[str, list[tuple[int, str]]] = {role: [] for role in CHANNEL_ORDER}

    for entry in entries:
        if entry.domain not in ALLOWED_SOURCE_DOMAINS:
            continue
        state = hass.states.get(entry.entity_id)
        if state is None:
            continue
        device_class = state.attributes.get("device_class")
        state_class = state.attributes.get("state_class")
        for role in CHANNEL_ORDER:
            if device_class != CHANNEL_SPECS[role].device_class:
                continue
            # Prioritas: state_class yang paling cocok dulu, lalu urutan abjad.
            expected = CHANNEL_SPECS[role].expected_state_classes
            rank = expected.index(state_class) if state_class in expected else len(
                expected
            )
            by_role[role].append((rank, entry.entity_id))

    suggestions: dict[str, Any] = {}
    for role, candidates in by_role.items():
        if not candidates:
            continue
        candidates.sort()
        suggestions[CHANNEL_CONF_KEYS[role]] = candidates[0][1]

    device_registry_name = None
    from homeassistant.helpers import device_registry as dr  # noqa: PLC0415

    device = dr.async_get(hass).async_get(device_id)
    if device is not None:
        device_registry_name = device.name_by_user or device.name
    if device_registry_name:
        suggestions[CONF_NAME] = device_registry_name
    return suggestions


def _format_report(hass: HomeAssistant, report: SourceReport) -> str:
    """Rakit laporan pemeriksaan jadi satu blok markdown untuk halaman review."""
    language = pick_language(hass.config.language)
    labels = ROLE_LABELS[language]
    texts = REPORT_TEXTS[language]
    lines: list[str] = [f"### {report.name}", ""]

    for role in CHANNEL_ORDER:
        channel = report.channels.get(role)
        label = labels[role]
        if channel is None or not channel.configured:
            lines.append(f"**{label}** - _{texts['not_configured']}_")
            lines.append("")
            continue

        lines.append(f"**{label}** - `{channel.entity_id}`")
        if channel.raw_value is not None:
            unit = channel.source_unit or ""
            detail = f"{texts['reading_now']}: {channel.raw_value:g} {unit}".strip()
            if channel.source_state_class:
                detail += f" · {channel.source_state_class}"
            lines.append(detail)
        if not channel.issues:
            lines.append(texts["ok"])
        for issue in channel.issues:
            marker = SEVERITY_MARKERS.get(issue.severity, "")
            lines.append(
                f"{marker} {issue_text(language, issue.code, issue.placeholders)}"
            )
        lines.append("")

    for issue in report.issues:
        marker = SEVERITY_MARKERS.get(issue.severity, "")
        lines.append(
            f"{marker} {issue_text(language, issue.code, issue.placeholders)}"
        )

    source_of_truth = report.energy_source_of_truth
    if source_of_truth == "cumulative":
        lines.append(texts["source_of_truth_cumulative"])
    elif source_of_truth == "integrated_from_power":
        lines.append(texts["source_of_truth_integrated"])

    return "\n".join(lines)


def _format_group_report(
    hass: HomeAssistant,
    group_input: dict[str, Any],
    sources: dict[str, str],
    warnings: list[Issue],
) -> str:
    """Rakit ringkasan Billing Group untuk halaman review."""
    language = pick_language(hass.config.language)
    texts = REPORT_TEXTS[language]
    period_labels = PERIOD_LABELS[language]
    lines: list[str] = [f"### {group_input.get(CONF_NAME, '')}", ""]

    lines.append(f"**{texts['members_header']}**")
    for source_id in group_input.get(CONF_SOURCE_IDS, []):
        lines.append(f"- {sources.get(source_id, source_id)}")
    lines.append("")

    cycle_config = CycleConfig.from_dict(group_input)
    now = dt_util.now()
    lines.append(f"**{texts['periods_header']}**")
    for period in group_input.get(CONF_CYCLE_PERIODS, []):
        upcoming = next_cycle_start(period, now, cycle_config)
        lines.append(
            f"- {period_labels.get(period, period)} — {texts['resets_at']}: "
            f"{upcoming.strftime('%d %b %Y %H:%M')}"
        )
    lines.append("")

    for issue in warnings:
        marker = SEVERITY_MARKERS.get(issue.severity, "")
        lines.append(
            f"{marker} {issue_text(language, issue.code, issue.placeholders)}"
        )

    return "\n".join(lines)


class _SourceFlowMixin:
    """Langkah-langkah yang dipakai bersama oleh config flow dan subentry flow."""

    hass: HomeAssistant
    _source_input: dict[str, Any]
    _source_report: SourceReport

    def _existing_source_names(self) -> set[str]:
        """Nama source yang sudah dipakai, untuk cek duplikat."""
        return set()

    async def async_step_pick_device(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Langkah 1: pilih perangkat sumber (opsional)."""
        if user_input is None:
            return self.async_show_form(
                step_id="pick_device",
                data_schema=_pick_device_schema(self._source_input),
            )

        self._source_input[CONF_SHOW_ALL_SENSORS] = user_input.get(
            CONF_SHOW_ALL_SENSORS, False
        )
        device_id = user_input.get(CONF_DEVICE_ID)
        if device_id:
            self._source_input[CONF_DEVICE_ID] = device_id
            for key, value in _suggest_from_device(self.hass, device_id).items():
                self._source_input.setdefault(key, value)
        return await self.async_step_map_entities()

    async def async_step_map_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Langkah 2: petakan sensor ke peran kanonik."""
        errors: dict[str, str] = {}
        show_all = bool(self._source_input.get(CONF_SHOW_ALL_SENSORS, False))

        if user_input is not None:
            candidate = dict(user_input)
            candidate[CONF_SHOW_ALL_SENSORS] = show_all
            if device_id := self._source_input.get(CONF_DEVICE_ID):
                candidate[CONF_DEVICE_ID] = device_id

            report = inspect_source(self.hass, candidate)

            name = str(candidate.get(CONF_NAME, "")).strip()
            if name and name in self._existing_source_names():
                report.issues.append(
                    Issue(SEVERITY_ERROR, "name_duplicate", {"name": name})
                )

            own_entities = set(_own_entity_ids(self.hass))
            for conf_key in (*CHANNEL_CONF_KEYS.values(), CONF_AVAILABILITY_ENTITY_ID):
                entity_id = candidate.get(conf_key)
                if entity_id in own_entities:
                    report.issues.append(
                        Issue(
                            SEVERITY_ERROR,
                            "own_entity_selected",
                            {"entity_id": str(entity_id)},
                        )
                    )

            if report.errors:
                self._source_input.update(candidate)
                errors["base"] = report.errors[0].code
            else:
                self._source_input.update(candidate)
                self._source_report = report
                return await self.async_step_review()

        return self.async_show_form(
            step_id="map_entities",
            data_schema=_map_entities_schema(
                self.hass, self._source_input, show_all
            ),
            errors=errors,
        )

    async def async_step_review(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Langkah 3: tampilkan hasil pemeriksaan, lalu simpan."""
        if user_input is None:
            return self.async_show_form(
                step_id="review",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "summary": _format_report(self.hass, self._source_report)
                },
            )
        return await self._async_finish_source()

    async def _async_finish_source(self) -> Any:
        """Simpan hasil - diisi oleh masing-masing flow."""
        raise NotImplementedError

    def _source_data(self) -> dict[str, Any]:
        """Data bersih yang disimpan ke subentry."""
        data = {
            key: value
            for key, value in self._source_input.items()
            if key != CONF_DEVICE_ID and value is not None
        }
        data[CONF_NAME] = str(data.get(CONF_NAME, "")).strip()
        return data


class PlnPrepaidMonitorConfigFlow(_SourceFlowMixin, ConfigFlow, domain=DOMAIN):
    """Config flow untuk entry induk (satu per instalasi)."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Halaman pembuka: penjelasan singkat + pilihan lanjut."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        self._source_input = {}
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {vol.Required(CONF_ADD_SOURCE_NOW, default=True): BooleanSelector()}
                ),
            )

        if not user_input.get(CONF_ADD_SOURCE_NOW, True):
            return self.async_create_entry(title="PLN Prepaid Monitor", data={})
        return await self.async_step_pick_device()

    async def _async_finish_source(self) -> ConfigFlowResult:
        """Buat entry induk sekaligus Energy Source pertama."""
        data = self._source_data()
        subentry = ConfigSubentryData(
            data=data,
            subentry_type=SUBENTRY_TYPE_ENERGY_SOURCE,
            title=data[CONF_NAME],
            unique_id=None,
        )
        return self.async_create_entry(
            title="PLN Prepaid Monitor", data={}, subentries=[subentry]
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: Any
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Jenis subentry yang bisa ditambahkan user dari halaman integrasi."""
        return {
            SUBENTRY_TYPE_ENERGY_SOURCE: EnergySourceSubentryFlowHandler,
            SUBENTRY_TYPE_BILLING_GROUP: BillingGroupSubentryFlowHandler,
        }


class EnergySourceSubentryFlowHandler(_SourceFlowMixin, ConfigSubentryFlow):
    """Flow untuk menambah dan mengedit satu Energy Source."""

    def _existing_source_names(self) -> set[str]:
        """Nama source lain di entry yang sama."""
        entry = self._get_entry()
        skip_id = (
            self._reconfigure_subentry_id
            if self.source == SOURCE_RECONFIGURE
            else None
        )
        return {
            str(subentry.data.get(CONF_NAME, "")).strip()
            for subentry_id, subentry in entry.subentries.items()
            if subentry_id != skip_id
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Tambah Energy Source baru."""
        self._source_input = {}
        return await self.async_step_pick_device()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit Energy Source yang sudah ada."""
        self._source_input = dict(self._get_reconfigure_subentry().data)
        return await self.async_step_map_entities()

    async def _async_finish_source(self) -> SubentryFlowResult:
        """Simpan subentry baru atau perbarui yang lama."""
        data = self._source_data()
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                title=data[CONF_NAME],
                data=data,
            )
        return self.async_create_entry(title=data[CONF_NAME], data=data)


def _billing_group_members_schema(
    source_options: list[SelectOptionDict], defaults: dict[str, Any]
) -> vol.Schema:
    """Skema langkah 1 Billing Group: nama dan sumber mana saja yang digabung."""
    return vol.Schema(
        {
            vol.Required(
                CONF_NAME, description={"suggested_value": defaults.get(CONF_NAME)}
            ): TextSelector(),
            vol.Required(
                CONF_SOURCE_IDS,
                description={"suggested_value": defaults.get(CONF_SOURCE_IDS, [])},
            ): SelectSelector(
                SelectSelectorConfig(
                    options=source_options,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            ),
        }
    )


def _billing_group_cycles_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Skema langkah 2 Billing Group: periode dan di mana batasnya jatuh."""
    return vol.Schema(
        {
            vol.Required(
                CONF_CYCLE_PERIODS,
                default=list(defaults.get(CONF_CYCLE_PERIODS, DEFAULT_CYCLE_PERIODS)),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(ALL_PERIODS),
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                    translation_key="cycle_period",
                )
            ),
            vol.Required(
                CONF_DAY_START_TIME,
                default=defaults.get(CONF_DAY_START_TIME, DEFAULT_DAY_START_TIME),
            ): TimeSelector(),
            vol.Required(
                CONF_WEEK_START_DAY,
                default=defaults.get(CONF_WEEK_START_DAY, DEFAULT_WEEK_START_DAY),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(WEEKDAYS), translation_key="week_start_day"
                )
            ),
            vol.Required(
                CONF_MONTH_START_DAY,
                default=defaults.get(CONF_MONTH_START_DAY, DEFAULT_MONTH_START_DAY),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=MAX_MONTH_START_DAY, step=1, mode=NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_YEAR_START_MONTH,
                default=defaults.get(CONF_YEAR_START_MONTH, DEFAULT_YEAR_START_MONTH),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(MONTHS), translation_key="year_start_month"
                )
            ),
        }
    )


class BillingGroupSubentryFlowHandler(ConfigSubentryFlow):
    """Flow untuk menambah dan mengedit satu Billing Group.

    Billing Group adalah "satu tagihan listrik": satu atau beberapa sumber
    energi yang pemakaiannya dihitung bersama-sama. Contoh khas untuk dua
    meteran terpisah: satu grup untuk rumah, satu grup untuk toko.
    """

    _group_input: dict[str, Any]
    _group_warnings: list[Issue]

    def _energy_sources(self) -> dict[str, str]:
        """Semua Energy Source yang ada, dipetakan id ke nama."""
        return {
            subentry_id: str(subentry.data.get(CONF_NAME, "")) or subentry.title
            for subentry_id, subentry in self._get_entry().subentries.items()
            if subentry.subentry_type == SUBENTRY_TYPE_ENERGY_SOURCE
        }

    def _skip_subentry_id(self) -> str | None:
        """Subentry yang sedang diedit, supaya tidak dibandingkan dengan dirinya."""
        if self.source == SOURCE_RECONFIGURE:
            return self._reconfigure_subentry_id
        return None

    def _existing_group_names(self) -> set[str]:
        """Nama Billing Group lain, untuk cek duplikat."""
        skip_id = self._skip_subentry_id()
        return {
            str(subentry.data.get(CONF_NAME, "")).strip()
            for subentry_id, subentry in self._get_entry().subentries.items()
            if subentry.subentry_type == SUBENTRY_TYPE_BILLING_GROUP
            and subentry_id != skip_id
        }

    def _overlap_warnings(self, source_ids: list[str]) -> list[Issue]:
        """Peringatan lunak kalau sumber sudah dipakai grup lain (spec K.11)."""
        skip_id = self._skip_subentry_id()
        sources = self._energy_sources()
        warnings: list[Issue] = []
        for subentry_id, subentry in self._get_entry().subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_BILLING_GROUP:
                continue
            if subentry_id == skip_id:
                continue
            shared = set(subentry.data.get(CONF_SOURCE_IDS) or []) & set(source_ids)
            for source_id in sorted(shared):
                warnings.append(
                    Issue(
                        SEVERITY_WARNING,
                        "source_used_by_other_group",
                        {
                            "source": sources.get(source_id, source_id),
                            "group": str(subentry.data.get(CONF_NAME, ""))
                            or subentry.title,
                        },
                    )
                )
        return warnings

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Tambah Billing Group baru."""
        if not self._energy_sources():
            return self.async_abort(reason="no_energy_sources")
        self._group_input = {}
        self._group_warnings = []
        return await self.async_step_members()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit Billing Group yang sudah ada."""
        self._group_input = dict(self._get_reconfigure_subentry().data)
        self._group_warnings = []
        return await self.async_step_members()

    async def async_step_members(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Langkah 1: nama grup dan sumber energi anggotanya."""
        errors: dict[str, str] = {}

        if user_input is not None:
            name = str(user_input.get(CONF_NAME, "")).strip()
            source_ids = list(user_input.get(CONF_SOURCE_IDS) or [])
            self._group_input.update({CONF_NAME: name, CONF_SOURCE_IDS: source_ids})

            if not name:
                errors["base"] = "name_required"
            elif name in self._existing_group_names():
                errors["base"] = "name_duplicate"
            elif not source_ids:
                errors["base"] = "no_sources_selected"
            else:
                self._group_warnings = self._overlap_warnings(source_ids)
                return await self.async_step_cycles()

        options = [
            SelectOptionDict(value=source_id, label=name)
            for source_id, name in self._energy_sources().items()
        ]
        return self.async_show_form(
            step_id="members",
            data_schema=_billing_group_members_schema(options, self._group_input),
            errors=errors,
        )

    async def async_step_cycles(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Langkah 2: periode apa saja, dan di mana batas siklusnya jatuh."""
        errors: dict[str, str] = {}

        if user_input is not None:
            periods = list(user_input.get(CONF_CYCLE_PERIODS) or [])
            if not periods:
                errors["base"] = "no_periods_selected"
            else:
                self._group_input.update(user_input)
                self._group_input[CONF_CYCLE_PERIODS] = periods
                self._group_input[CONF_MONTH_START_DAY] = int(
                    user_input.get(CONF_MONTH_START_DAY, DEFAULT_MONTH_START_DAY)
                )
                return await self.async_step_review()

        return self.async_show_form(
            step_id="cycles",
            data_schema=_billing_group_cycles_schema(self._group_input),
            errors=errors,
        )

    async def async_step_review(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Langkah 3: ringkasan sebelum disimpan."""
        if user_input is None:
            return self.async_show_form(
                step_id="review",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "summary": _format_group_report(
                        self.hass,
                        self._group_input,
                        self._energy_sources(),
                        self._group_warnings,
                    )
                },
            )

        data = dict(self._group_input)
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                title=data[CONF_NAME],
                data=data,
            )
        return self.async_create_entry(title=data[CONF_NAME], data=data)
