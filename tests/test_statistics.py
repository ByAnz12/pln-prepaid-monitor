"""Test bahwa entity kita benar-benar masuk long-term statistics recorder.

Ini yang membuat grafik histori hari/minggu/bulan/tahun di dashboard nanti
punya data. Long-term statistics tidak pernah di-purge oleh Home Assistant,
jadi entity kita harus terdaftar dengan ``device_class`` dan ``state_class``
yang benar sejak awal - kalau salah, datanya tidak terkumpul dan tidak bisa
diperbaiki surut.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
    do_adhoc_statistics,
)

from custom_components.pln_prepaid_monitor.const import (
    CONF_CYCLE_PERIODS,
    CONF_ENERGY_ENTITY_ID,
    CONF_POWER_ENTITY_ID,
    CONF_SOURCE_IDS,
    DOMAIN,
    SUBENTRY_TYPE_BILLING_GROUP,
    SUBENTRY_TYPE_ENERGY_SOURCE,
)

from .conftest import apply_states, MCB_RUMAH

RUMAH_ID = "src_rumah"

SUBENTRIES = [
    {
        "data": {
            "name": "MCB RUMAH",
            CONF_ENERGY_ENTITY_ID: "sensor.mcb_rumah_total_energy",
            CONF_POWER_ENTITY_ID: "sensor.mcb_rumah_phase_a_power",
        },
        "subentry_id": RUMAH_ID,
        "subentry_type": SUBENTRY_TYPE_ENERGY_SOURCE,
        "title": "MCB RUMAH",
        "unique_id": None,
    },
    {
        "data": {
            "name": "PLN RUMAH",
            CONF_SOURCE_IDS: [RUMAH_ID],
            CONF_CYCLE_PERIODS: ["day", "month"],
        },
        "subentry_id": "grp_rumah",
        "subentry_type": SUBENTRY_TYPE_BILLING_GROUP,
        "title": "PLN RUMAH",
        "unique_id": None,
    },
]


async def _setup(hass: HomeAssistant):
    """Pasang integrasi lengkap dengan satu sumber dan satu Billing Group."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    await hass.config.async_set_time_zone("Asia/Jakarta")
    entry = MockConfigEntry(domain=DOMAIN, data={}, subentries_data=SUBENTRIES)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_energy_entities_have_statistics_metadata(
    recorder_mock, hass: HomeAssistant
) -> None:
    """Recorder harus mengenali entity energi kita sebagai sumber statistik."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass)
    await async_wait_recording_done(hass)

    from homeassistant.components.recorder.statistics import list_statistic_ids

    statistic_ids = await hass.async_add_executor_job(list_statistic_ids, hass)
    found = {row["statistic_id"] for row in statistic_ids}

    for entity_id in (
        "sensor.mcb_rumah_energy",
        "sensor.pln_rumah_energy_total",
        "sensor.pln_rumah_energy_this_day",
        "sensor.pln_rumah_energy_this_month",
    ):
        assert entity_id in found, entity_id


async def test_long_term_statistics_records_consumption(
    recorder_mock, hass: HomeAssistant, freezer
) -> None:
    """Pemakaian yang tercatat harus muncul di kolom ``sum`` statistik."""
    freezer.move_to("2026-09-03 01:00:00+00:00")
    apply_states(hass, MCB_RUMAH)
    await _setup(hass)
    await async_wait_recording_done(hass)

    period_start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    do_adhoc_statistics(hass, start=period_start)
    await async_wait_recording_done(hass)

    # Pemakaian 4 kWh pada jam berikutnya.
    freezer.tick(timedelta(hours=1))
    hass.states.async_set(
        "sensor.mcb_rumah_total_energy",
        "15502.27",
        MCB_RUMAH["sensor.mcb_rumah_total_energy"][1],
    )
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    do_adhoc_statistics(hass, start=period_start + timedelta(hours=1))
    await async_wait_recording_done(hass)

    # Dibaca lewat periode 5 menit, sama seperti test resmi Home Assistant
    # sendiri: kompaksi ke tabel per-jam dijalankan terpisah oleh recorder.
    stats = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        period_start,
        None,
        {"sensor.pln_rumah_energy_total"},
        "5minute",
        None,
        {"sum", "state"},
    )

    rows = stats["sensor.pln_rumah_energy_total"]
    assert len(rows) >= 2
    # Angka meteran terakhir tercatat apa adanya...
    assert rows[-1]["state"] == pytest.approx(15502.27)
    # ...dan konsumsinya tercatat sebagai 4 kWh, bukan 15.502.
    assert rows[-1]["sum"] == pytest.approx(4.0)


async def test_period_counter_statistics_use_cycle_boundaries(
    recorder_mock, hass: HomeAssistant, freezer
) -> None:
    """Penghitung periode terdaftar sebagai ``total`` dengan ``last_reset``.

    Kombinasi inilah yang membuat Home Assistant paham bahwa nilainya kembali
    ke nol tiap siklus, bukan menganggapnya counter yang rusak.
    """
    freezer.move_to("2026-09-03 01:00:00+00:00")
    apply_states(hass, MCB_RUMAH)
    await _setup(hass)
    await async_wait_recording_done(hass)

    from homeassistant.components.recorder.statistics import list_statistic_ids

    statistic_ids = await hass.async_add_executor_job(list_statistic_ids, hass)
    rows = {row["statistic_id"]: row for row in statistic_ids}

    daily = rows["sensor.pln_rumah_energy_this_day"]
    assert daily["has_sum"] is True
    assert daily["statistics_unit_of_measurement"] == "kWh"

    state = hass.states.get("sensor.pln_rumah_energy_this_day")
    assert state.attributes["last_reset"] is not None
    assert state.attributes["state_class"] == "total"


async def test_measurement_entities_are_recorded_as_mean(
    recorder_mock, hass: HomeAssistant
) -> None:
    """Daya dicatat sebagai rata-rata, bukan dijumlahkan sebagai konsumsi."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass)
    await async_wait_recording_done(hass)

    from homeassistant.components.recorder.statistics import list_statistic_ids

    statistic_ids = await hass.async_add_executor_job(list_statistic_ids, hass)
    rows = {row["statistic_id"]: row for row in statistic_ids}

    power = rows["sensor.pln_rumah_power"]
    assert power["has_sum"] is False
    assert power["statistics_unit_of_measurement"] == "W"


async def test_cost_entities_are_recorded_as_total(
    recorder_mock, hass: HomeAssistant
) -> None:
    """Sensor biaya harus punya kolom sum, supaya grafik biaya bisa dibuat."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.pln_prepaid_monitor.const import (
        CONF_FIXED_CHARGE_PERIOD,
        CONF_FIXED_CHARGE_RP,
        CONF_RATE_HISTORY,
        CONF_RATE_RP_PER_KWH,
        CONF_ROUNDING_MODE,
        CONF_ROUNDING_UNIT_RP,
        CONF_TARIFF_ID,
        SUBENTRY_TYPE_TARIFF,
    )

    await hass.config.async_set_time_zone("Asia/Jakarta")
    await hass.config.async_update(currency="IDR")
    apply_states(hass, MCB_RUMAH)

    subentries = [
        SUBENTRIES[0],
        {
            "data": {
                "name": "Tarif R-1",
                CONF_RATE_RP_PER_KWH: 1444.70,
                CONF_FIXED_CHARGE_RP: 0.0,
                CONF_FIXED_CHARGE_PERIOD: "monthly",
                CONF_ROUNDING_MODE: "nearest",
                CONF_ROUNDING_UNIT_RP: 1.0,
                CONF_RATE_HISTORY: [],
            },
            "subentry_id": "tar_r1",
            "subentry_type": SUBENTRY_TYPE_TARIFF,
            "title": "Tarif R-1",
            "unique_id": None,
        },
        {
            "data": {**SUBENTRIES[1]["data"], CONF_TARIFF_ID: "tar_r1"},
            "subentry_id": "grp_rumah",
            "subentry_type": SUBENTRIES[1]["subentry_type"],
            "title": "PLN RUMAH",
            "unique_id": None,
        },
    ]
    entry = MockConfigEntry(domain=DOMAIN, data={}, subentries_data=subentries)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    from homeassistant.components.recorder.statistics import list_statistic_ids

    statistic_ids = await hass.async_add_executor_job(list_statistic_ids, hass)
    rows = {row["statistic_id"]: row for row in statistic_ids}

    cost = rows["sensor.pln_rumah_cost_total"]
    assert cost["has_sum"] is True
    assert cost["statistics_unit_of_measurement"] == "IDR"
