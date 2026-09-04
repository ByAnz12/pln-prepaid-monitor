"""Test entity prediksi, termasuk pembacaan long-term statistics sungguhan."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.pln_prepaid_monitor.const import (
    ATTR_CONFIDENCE,
    ATTR_DATA_POINTS,
    ATTR_WINDOW_USED,
    CONF_CRITICAL_THRESHOLD_DAYS,
    CONF_CYCLE_PERIODS,
    CONF_ENERGY_ENTITY_ID,
    CONF_FIXED_CHARGE_PERIOD,
    CONF_FIXED_CHARGE_RP,
    CONF_RATE_HISTORY,
    CONF_RATE_RP_PER_KWH,
    CONF_ROUNDING_MODE,
    CONF_ROUNDING_UNIT_RP,
    CONF_SAFETY_MARGIN_PERCENT,
    CONF_SOURCE_IDS,
    CONF_TARIFF_ID,
    CONF_TOKEN_ENABLED,
    CONF_TOKEN_LOW_KWH_THRESHOLD,
    CONF_VERY_CRITICAL_THRESHOLD_DAYS,
    CONF_WARNING_THRESHOLD_DAYS,
    DOMAIN,
    SERVICE_ADD_TOKEN_TOPUP,
    SUBENTRY_TYPE_BILLING_GROUP,
    SUBENTRY_TYPE_ENERGY_SOURCE,
    SUBENTRY_TYPE_TARIFF,
)

from .conftest import apply_states, MCB_RUMAH

RUMAH_ID = "src_rumah"
TARIFF_ID = "tar_r1"
GROUP_ID = "grp_rumah"
GROUP_ENERGY_ENTITY = "sensor.pln_rumah_energy_total"

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


def _group_subentry(**overrides) -> dict:
    """Kelompok tagihan dengan token dan ambang bawaan."""
    data = {
        "name": "PLN RUMAH",
        CONF_SOURCE_IDS: [RUMAH_ID],
        CONF_CYCLE_PERIODS: ["day"],
        CONF_TARIFF_ID: TARIFF_ID,
        CONF_TOKEN_ENABLED: True,
        CONF_WARNING_THRESHOLD_DAYS: 7.0,
        CONF_CRITICAL_THRESHOLD_DAYS: 3.0,
        CONF_VERY_CRITICAL_THRESHOLD_DAYS: 1.0,
        CONF_TOKEN_LOW_KWH_THRESHOLD: 0.0,
        CONF_SAFETY_MARGIN_PERCENT: 0.0,
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


async def _topup(hass: HomeAssistant, kwh: float) -> None:
    """Catat pengisian token lewat layanan."""
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, GROUP_ID)})
    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_TOKEN_TOPUP,
        {"device_id": [device.id], "kwh_credited": kwh},
        blocking=True,
    )
    await hass.async_block_till_done()


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Paksa perhitungan ulang perkiraan."""
    await entry.runtime_data.billing_groups[GROUP_ID].async_refresh_prediction()
    await hass.async_block_till_done()


def _fake_samples(**windows):
    """Ganti pembacaan statistik dengan sampel yang sudah ditentukan."""

    async def _fetch(hass, statistic_id, config, now):
        return dict(windows)

    return patch(
        "custom_components.pln_prepaid_monitor.coordinator."
        "async_fetch_window_samples",
        new=_fetch,
    )


async def test_prediction_entities_created(hass: HomeAssistant) -> None:
    """Kelompok bertoken mendapat seluruh entity perkiraan."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())

    for entity_id in (
        "sensor.pln_rumah_average_daily_usage",
        "sensor.pln_rumah_estimated_days_remaining",
        "sensor.pln_rumah_estimated_empty_date",
        "sensor.pln_rumah_token_status",
        "binary_sensor.pln_rumah_data_sufficient",
    ):
        assert hass.states.get(entity_id) is not None, entity_id


async def test_average_usage_exists_without_token(hass: HomeAssistant) -> None:
    """Rata-rata pemakaian berguna walaupun token tidak dicatat."""
    apply_states(hass, MCB_RUMAH)
    await _setup(
        hass,
        SOURCE_SUBENTRY,
        TARIFF_SUBENTRY,
        _group_subentry(**{CONF_TOKEN_ENABLED: False}),
    )

    assert hass.states.get("sensor.pln_rumah_average_daily_usage") is not None
    assert hass.states.get("binary_sensor.pln_rumah_data_sufficient") is not None
    # Yang khusus token tidak dibuat.
    assert hass.states.get("sensor.pln_rumah_estimated_days_remaining") is None
    assert hass.states.get("sensor.pln_rumah_token_status") is None


async def test_no_data_means_no_numbers_at_all(hass: HomeAssistant) -> None:
    """Tanpa riwayat, semua angka perkiraan kosong - tidak ada tebakan."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())
    await _topup(hass, 50.0)

    assert hass.states.get("binary_sensor.pln_rumah_data_sufficient").state == "off"
    assert hass.states.get("sensor.pln_rumah_average_daily_usage").state == "unavailable"
    assert (
        hass.states.get("sensor.pln_rumah_estimated_days_remaining").state
        == "unavailable"
    )
    assert (
        hass.states.get("sensor.pln_rumah_estimated_empty_date").state == "unavailable"
    )
    assert hass.states.get("sensor.pln_rumah_token_status").state == "unknown"


async def test_prediction_from_seven_days_of_usage(hass: HomeAssistant) -> None:
    """Tujuh hari pemakaian 10 kWh menghasilkan perkiraan berkeyakinan tinggi."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())
    await _topup(hass, 50.0)

    with _fake_samples(**{"7d": [10.0] * 7}):
        await _refresh(hass, entry)

    assert hass.states.get("binary_sensor.pln_rumah_data_sufficient").state == "on"

    average = hass.states.get("sensor.pln_rumah_average_daily_usage")
    assert float(average.state) == pytest.approx(10.0)
    assert average.attributes[ATTR_WINDOW_USED] == "7d"
    assert average.attributes[ATTR_DATA_POINTS] == 7
    assert average.attributes[ATTR_CONFIDENCE] == "high"

    days = hass.states.get("sensor.pln_rumah_estimated_days_remaining")
    assert float(days.state) == pytest.approx(5.0)
    assert days.attributes["device_class"] == "duration"

    empty_date = hass.states.get("sensor.pln_rumah_estimated_empty_date")
    assert empty_date.attributes["device_class"] == "timestamp"
    assert empty_date.state != "unavailable"


async def test_status_levels_react_to_remaining_days(hass: HomeAssistant) -> None:
    """Status naik tingkat mengikuti sisa hari."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())

    # 10 kWh sisa, pemakaian 10 kWh/hari -> 1 hari lagi -> sangat kritis.
    await _topup(hass, 10.0)
    with _fake_samples(**{"7d": [10.0] * 7}):
        await _refresh(hass, entry)
    assert hass.states.get("sensor.pln_rumah_token_status").state == "very_critical"

    # Isi 40 kWh lagi -> 5 hari -> perlu perhatian.
    await _topup(hass, 40.0)
    with _fake_samples(**{"7d": [10.0] * 7}):
        await _refresh(hass, entry)
    assert hass.states.get("sensor.pln_rumah_token_status").state == "warning"

    # Isi banyak -> aman.
    await _topup(hass, 200.0)
    with _fake_samples(**{"7d": [10.0] * 7}):
        await _refresh(hass, entry)
    assert hass.states.get("sensor.pln_rumah_token_status").state == "normal"


async def test_zero_usage_does_not_produce_infinite_days(
    hass: HomeAssistant,
) -> None:
    """Pemakaian nol: hari tersisa tetap kosong, bukan angka tak berhingga."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())
    await _topup(hass, 50.0)

    with _fake_samples(**{"7d": [0.0] * 7}):
        await _refresh(hass, entry)

    assert float(hass.states.get("sensor.pln_rumah_average_daily_usage").state) == (
        pytest.approx(0.0)
    )
    assert (
        hass.states.get("sensor.pln_rumah_estimated_days_remaining").state
        == "unavailable"
    )
    assert hass.states.get("sensor.pln_rumah_token_status").state == "unknown"


async def test_fallback_window_lowers_confidence(hass: HomeAssistant) -> None:
    """Data 7 hari belum cukup: turun ke 24 jam, keyakinan jadi sedang."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())
    await _topup(hass, 50.0)

    with _fake_samples(**{"7d": [10.0], "24h": [0.5] * 24}):
        await _refresh(hass, entry)

    average = hass.states.get("sensor.pln_rumah_average_daily_usage")
    assert average.attributes[ATTR_WINDOW_USED] == "24h"
    assert average.attributes[ATTR_CONFIDENCE] == "medium"
    assert float(average.state) == pytest.approx(12.0)


async def test_absolute_kwh_threshold_overrides_days(hass: HomeAssistant) -> None:
    """Sisa kWh sangat sedikit langsung sangat kritis walau harinya masih banyak."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(
        hass,
        SOURCE_SUBENTRY,
        TARIFF_SUBENTRY,
        _group_subentry(**{CONF_TOKEN_LOW_KWH_THRESHOLD: 5.0}),
    )
    await _topup(hass, 4.0)

    # Pemakaian sangat kecil, jadi hari tersisa terlihat banyak.
    with _fake_samples(**{"7d": [0.1] * 7}):
        await _refresh(hass, entry)

    assert float(hass.states.get("sensor.pln_rumah_estimated_days_remaining").state) > 7
    assert hass.states.get("sensor.pln_rumah_token_status").state == "very_critical"


async def test_status_shows_hold_while_ledger_frozen(hass: HomeAssistant) -> None:
    """Selama ledger dibekukan, status tidak berpura-pura tahu keadaan."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())
    await _topup(hass, 50.0)

    with _fake_samples(**{"7d": [10.0] * 7}):
        await _refresh(hass, entry)
    assert hass.states.get("sensor.pln_rumah_token_status").state == "warning"

    # Meteran diganti: ledger ditahan.
    hass.states.async_set(
        "sensor.mcb_rumah_total_energy",
        "9000",
        MCB_RUMAH["sensor.mcb_rumah_total_energy"][1],
    )
    await hass.async_block_till_done()

    assert hass.states.get("sensor.pln_rumah_token_status").state == "hold"


async def test_safety_margin_shortens_the_estimate(hass: HomeAssistant) -> None:
    """Margin aman membuat perkiraan sedikit lebih cepat habis."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(
        hass,
        SOURCE_SUBENTRY,
        TARIFF_SUBENTRY,
        _group_subentry(**{CONF_SAFETY_MARGIN_PERCENT: 10.0}),
    )
    await _topup(hass, 110.0)

    with _fake_samples(**{"7d": [10.0] * 7}):
        await _refresh(hass, entry)

    # 110 kWh / (10 kWh x 1,1) = 10 hari, bukan 11.
    assert float(hass.states.get("sensor.pln_rumah_estimated_days_remaining").state) == (
        pytest.approx(10.0)
    )


async def test_reads_real_long_term_statistics(
    recorder_mock, hass: HomeAssistant, freezer
) -> None:
    """Jalur sungguhan: statistik recorder dibaca dan jadi rata-rata harian.

    Test lain memakai sampel palsu untuk menguji aturannya; test ini memastikan
    pembacaan dari database recorder benar-benar bekerja.
    """
    from homeassistant.components.recorder.statistics import async_import_statistics

    freezer.move_to("2026-09-10 05:00:00+00:00")
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group_subentry())
    await _topup(hass, 50.0)

    # Tujuh hari pemakaian 10 kWh/hari, ditulis sebagai statistik per jam.
    start = (dt_util.utcnow() - timedelta(days=7)).replace(
        minute=0, second=0, microsecond=0
    )
    running_sum = 0.0
    rows = []
    for hour in range(7 * 24):
        running_sum += 10.0 / 24
        rows.append(
            {
                "start": start + timedelta(hours=hour),
                "state": 15498.27 + running_sum,
                "sum": running_sum,
            }
        )

    async_import_statistics(
        hass,
        {
            "has_mean": False,
            "has_sum": True,
            "mean_type": 0,
            "name": None,
            "source": "recorder",
            "statistic_id": GROUP_ENERGY_ENTITY,
            "unit_class": "energy",
            "unit_of_measurement": "kWh",
        },
        rows,
    )
    await async_wait_recording_done(hass)

    await _refresh(hass, entry)

    average = hass.states.get("sensor.pln_rumah_average_daily_usage")
    assert float(average.state) == pytest.approx(10.0, abs=0.5)
    assert average.attributes[ATTR_WINDOW_USED] == "7d"
    assert hass.states.get("binary_sensor.pln_rumah_data_sufficient").state == "on"
