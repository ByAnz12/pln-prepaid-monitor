"""Source Normalization: memeriksa dan menyeragamkan sensor sumber.

Tugas modul ini ada dua:

1. **Memeriksa** entity yang dipilih user (dipakai config flow untuk halaman
   "Cek dulu sebelum disimpan") - satuan apa, device_class apa, state_class
   apa, sedang tersedia atau tidak.
2. **Menyeragamkan** nilainya ke satuan kanonik: kWh, W, V, A, Hz.

Kenapa ini tidak boleh sekadar pass-through: pada instance user ini saja sudah
ada dua kasus nyata yang membuktikannya (lihat spec Bagian O.3) -
``sensor.mcb_rumah_phase_a_power`` memakai satuan **kW** sementara sumber lain
memakai **W**, dan ``sensor.battery1_total_energy_meter`` punya
``device_class: energy`` **tanpa state_class sama sekali**.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.sensor import ATTR_STATE_CLASS, SensorStateClass
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_NAME,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.unit_conversion import (
    BaseUnitConverter,
    ElectricCurrentConverter,
    ElectricPotentialConverter,
    EnergyConverter,
    FrequencyConverter,
    PowerConverter,
)

from ..const import (
    CHANNEL_CONF_KEYS,
    CHANNEL_CURRENT,
    CHANNEL_ENERGY,
    CHANNEL_FREQUENCY,
    CHANNEL_POWER,
    CHANNEL_VOLTAGE,
    CONF_AVAILABILITY_ENTITY_ID,
)

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"


@dataclass(frozen=True)
class ChannelSpec:
    """Aturan kanonik untuk satu peran kanal."""

    role: str
    device_class: str
    target_unit: str
    converter: type[BaseUnitConverter]
    expected_state_classes: tuple[str, ...]


CHANNEL_SPECS: dict[str, ChannelSpec] = {
    CHANNEL_ENERGY: ChannelSpec(
        role=CHANNEL_ENERGY,
        device_class="energy",
        target_unit=UnitOfEnergy.KILO_WATT_HOUR,
        converter=EnergyConverter,
        expected_state_classes=(
            SensorStateClass.TOTAL_INCREASING,
            SensorStateClass.TOTAL,
        ),
    ),
    CHANNEL_POWER: ChannelSpec(
        role=CHANNEL_POWER,
        device_class="power",
        target_unit=UnitOfPower.WATT,
        converter=PowerConverter,
        expected_state_classes=(SensorStateClass.MEASUREMENT,),
    ),
    CHANNEL_VOLTAGE: ChannelSpec(
        role=CHANNEL_VOLTAGE,
        device_class="voltage",
        target_unit=UnitOfElectricPotential.VOLT,
        converter=ElectricPotentialConverter,
        expected_state_classes=(SensorStateClass.MEASUREMENT,),
    ),
    CHANNEL_CURRENT: ChannelSpec(
        role=CHANNEL_CURRENT,
        device_class="current",
        target_unit=UnitOfElectricCurrent.AMPERE,
        converter=ElectricCurrentConverter,
        expected_state_classes=(SensorStateClass.MEASUREMENT,),
    ),
    CHANNEL_FREQUENCY: ChannelSpec(
        role=CHANNEL_FREQUENCY,
        device_class="frequency",
        target_unit=UnitOfFrequency.HERTZ,
        converter=FrequencyConverter,
        expected_state_classes=(SensorStateClass.MEASUREMENT,),
    ),
}


@dataclass(frozen=True)
class Issue:
    """Satu temuan pada saat pemeriksaan sumber."""

    severity: str
    code: str
    placeholders: dict[str, str] = field(default_factory=dict)


@dataclass
class ChannelReport:
    """Hasil pemeriksaan satu kanal pada satu Energy Source."""

    role: str
    entity_id: str | None
    configured: bool = False
    exists: bool = False
    available: bool = False
    raw_state: str | None = None
    raw_value: float | None = None
    source_unit: str | None = None
    source_device_class: str | None = None
    source_state_class: str | None = None
    target_unit: str | None = None
    conversion_factor: float | None = None
    usable: bool = False
    issues: list[Issue] = field(default_factory=list)

    @property
    def normalized_value(self) -> float | None:
        """Nilai yang sudah dikonversi ke satuan kanonik."""
        if self.raw_value is None or self.conversion_factor is None:
            return None
        return self.raw_value * self.conversion_factor

    @property
    def has_error(self) -> bool:
        """True bila kanal ini punya temuan yang memblokir penyimpanan."""
        return any(issue.severity == SEVERITY_ERROR for issue in self.issues)


def _parse_float(value: Any) -> float | None:
    """Ubah state jadi float, atau None bila bukan angka."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def conversion_factor(
    spec: ChannelSpec, source_unit: str | None
) -> tuple[float | None, Issue | None]:
    """Hitung faktor pengali dari satuan sumber ke satuan kanonik.

    Semua satuan listrik yang dipakai di sini bersifat linier murni tanpa
    offset, jadi faktor cukup dihitung sekali dari nilai 1.0.
    """
    if source_unit is None:
        # Tanpa satuan kita tidak bisa mengonversi. Kita anggap sudah dalam
        # satuan kanonik, tapi user harus diberi tahu asumsi ini.
        return 1.0, Issue(
            SEVERITY_WARNING,
            "no_unit",
            {"target_unit": str(spec.target_unit)},
        )
    if source_unit == spec.target_unit:
        return 1.0, None
    try:
        factor = spec.converter.convert(1.0, source_unit, spec.target_unit)
    except HomeAssistantError:
        return None, Issue(
            SEVERITY_ERROR,
            "unit_not_convertible",
            {"unit": str(source_unit), "target_unit": str(spec.target_unit)},
        )
    return factor, Issue(
        SEVERITY_INFO,
        "unit_converted",
        {
            "unit": str(source_unit),
            "target_unit": str(spec.target_unit),
            "factor": f"{factor:g}",
        },
    )


def inspect_channel(
    hass: HomeAssistant, role: str, entity_id: str | None
) -> ChannelReport:
    """Periksa satu entity sumber dan laporkan apa adanya."""
    spec = CHANNEL_SPECS[role]
    report = ChannelReport(
        role=role, entity_id=entity_id, target_unit=str(spec.target_unit)
    )
    if not entity_id:
        return report

    report.configured = True
    state = hass.states.get(entity_id)
    if state is None:
        # Peringatan, bukan blokir: entity milik integrasi lain kadang belum
        # siap saat kita dikonfigurasi (spec Bagian E).
        report.issues.append(
            Issue(SEVERITY_WARNING, "entity_not_found", {"entity_id": entity_id})
        )
        return report

    report.exists = True
    report.raw_state = state.state
    report.source_unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    report.source_device_class = state.attributes.get(ATTR_DEVICE_CLASS)
    report.source_state_class = state.attributes.get(ATTR_STATE_CLASS)

    factor, factor_issue = conversion_factor(spec, report.source_unit)
    report.conversion_factor = factor
    if factor_issue is not None:
        report.issues.append(factor_issue)

    if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        report.issues.append(
            Issue(SEVERITY_WARNING, "entity_unavailable", {"entity_id": entity_id})
        )
    else:
        report.raw_value = _parse_float(state.state)
        if report.raw_value is None:
            report.issues.append(
                Issue(
                    SEVERITY_ERROR,
                    "state_not_numeric",
                    {"entity_id": entity_id, "state": state.state},
                )
            )
        else:
            report.available = True

    if (
        report.source_device_class is not None
        and report.source_device_class != spec.device_class
    ):
        report.issues.append(
            Issue(
                SEVERITY_WARNING,
                "device_class_mismatch",
                {
                    "device_class": str(report.source_device_class),
                    "expected": spec.device_class,
                },
            )
        )

    if role == CHANNEL_ENERGY:
        if report.source_state_class is None:
            # Kasus nyata: sensor.battery1_total_energy_meter (spec O.3).
            report.issues.append(
                Issue(SEVERITY_WARNING, "energy_no_state_class", {})
            )
        elif report.source_state_class == SensorStateClass.MEASUREMENT:
            report.issues.append(
                Issue(SEVERITY_WARNING, "energy_state_class_measurement", {})
            )

    report.usable = not report.has_error
    return report


@dataclass
class SourceReport:
    """Hasil pemeriksaan seluruh Energy Source."""

    name: str
    channels: dict[str, ChannelReport] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    @property
    def all_issues(self) -> list[Issue]:
        """Gabungan temuan tingkat sumber dan tingkat kanal."""
        found = list(self.issues)
        for channel in self.channels.values():
            found.extend(channel.issues)
        return found

    @property
    def errors(self) -> list[Issue]:
        """Temuan yang memblokir penyimpanan."""
        return [i for i in self.all_issues if i.severity == SEVERITY_ERROR]

    @property
    def energy_source_of_truth(self) -> str | None:
        """Dari mana angka kWh akan berasal: pembacaan kumulatif atau estimasi."""
        energy = self.channels.get(CHANNEL_ENERGY)
        if energy is not None and energy.configured and energy.usable:
            return "cumulative"
        power = self.channels.get(CHANNEL_POWER)
        if power is not None and power.configured and power.usable:
            return "integrated_from_power"
        return None


def inspect_source(hass: HomeAssistant, data: dict[str, Any]) -> SourceReport:
    """Periksa seluruh pemetaan entity milik satu Energy Source."""
    report = SourceReport(name=str(data.get(CONF_NAME, "") or ""))

    if not report.name.strip():
        report.issues.append(Issue(SEVERITY_ERROR, "name_required", {}))

    for role, conf_key in CHANNEL_CONF_KEYS.items():
        report.channels[role] = inspect_channel(hass, role, data.get(conf_key))

    energy = report.channels[CHANNEL_ENERGY]
    power = report.channels[CHANNEL_POWER]

    if not energy.configured and not power.configured:
        report.issues.append(Issue(SEVERITY_ERROR, "no_measurement_entity", {}))
    elif not energy.configured:
        # Fallback Riemann: sah, tapi user berhak tahu ini estimasi.
        report.issues.append(Issue(SEVERITY_WARNING, "energy_from_power", {}))

    availability_entity = data.get(CONF_AVAILABILITY_ENTITY_ID)
    if availability_entity and hass.states.get(availability_entity) is None:
        report.issues.append(
            Issue(
                SEVERITY_WARNING,
                "entity_not_found",
                {"entity_id": str(availability_entity)},
            )
        )

    return report
