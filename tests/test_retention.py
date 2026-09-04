"""Test pembersihan data lama.

Ini bagian paling rapuh di seluruh sistem - ia menyentuh struktur internal
recorder yang bukan API publik. Karena itu test di sini menjaga dua hal dengan
keras: bahwa **hanya** data milik integrasi ini yang tersentuh, dan bahwa
kegagalan terjadi terang-terangan, bukan diam-diam salah hapus.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.pln_prepaid_monitor.const import (
    CONF_AUTO_PURGE_ENABLED,
    CONF_CYCLE_PERIODS,
    CONF_ENERGY_ENTITY_ID,
    CONF_SOURCE_IDS,
    CONF_STATISTICS_RETENTION_YEARS,
    DOMAIN,
    SERVICE_PURGE_OLD_DATA,
    SUBENTRY_TYPE_BILLING_GROUP,
    SUBENTRY_TYPE_ENERGY_SOURCE,
)
from custom_components.pln_prepaid_monitor.retention import (
    RETENTION_UNLIMITED,
    RetentionUnsupportedError,
    check_supported,
    retention_days,
)

from .conftest import apply_states, MCB_RUMAH

RUMAH_ID = "src_rumah"
GROUP_ID = "grp_rumah"
OUR_ENTITY = "sensor.pln_rumah_energy_total"
FOREIGN_ENTITY = "sensor.meteran_tetangga"

SUBENTRIES = [
    {
        "data": {
            "name": "MCB RUMAH",
            CONF_ENERGY_ENTITY_ID: "sensor.mcb_rumah_total_energy",
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
            CONF_CYCLE_PERIODS: ["day"],
        },
        "subentry_id": GROUP_ID,
        "subentry_type": SUBENTRY_TYPE_BILLING_GROUP,
        "title": "PLN RUMAH",
        "unique_id": None,
    },
]


# --- aritmetika retensi ------------------------------------------------------


@pytest.mark.parametrize(
    ("choice", "expected"),
    [("1", 365.25), ("2", 730.5), ("5", 1826.25)],
)
def test_retention_days(choice: str, expected: float) -> None:
    """Pilihan tahun diubah jadi jumlah hari."""
    assert retention_days(choice) == pytest.approx(expected)


@pytest.mark.parametrize("choice", [RETENTION_UNLIMITED, None, "", "0", "abc"])
def test_unlimited_means_never_delete(choice: str | None) -> None:
    """Apa pun yang bukan angka tahun berarti tidak menghapus apa pun."""
    assert retention_days(choice) is None


def test_recorder_schema_is_still_what_we_expect() -> None:
    """Kanari untuk versi Home Assistant berikutnya.

    Kalau Home Assistant mengubah nama model atau kolom statistiknya, test ini
    gagal lebih dulu - jauh sebelum ada data user yang salah terhapus.
    """
    check_supported()


# --- pembersihan sungguhan ---------------------------------------------------


async def _setup(hass: HomeAssistant, **options) -> MockConfigEntry:
    """Pasang integrasi dengan pengaturan retensi tertentu."""
    await hass.config.async_set_time_zone("Asia/Jakarta")
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options=options, subentries_data=SUBENTRIES
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _import_rows(hass: HomeAssistant, entity_id: str, starts: list[datetime]) -> None:
    """Tulis beberapa baris statistik untuk satu entity."""
    from homeassistant.components.recorder.statistics import async_import_statistics

    async_import_statistics(
        hass,
        {
            "has_mean": False,
            "has_sum": True,
            "mean_type": 0,
            "name": None,
            "source": "recorder",
            "statistic_id": entity_id,
            "unit_class": "energy",
            "unit_of_measurement": "kWh",
        },
        [
            {"start": start, "state": 100.0 + index, "sum": float(index)}
            for index, start in enumerate(starts)
        ],
    )


def _count_rows(hass: HomeAssistant, entity_id: str) -> int:
    """Hitung baris statistik jangka panjang milik satu entity."""
    from homeassistant.components.recorder.db_schema import (
        Statistics,
        StatisticsMeta,
    )
    from homeassistant.components.recorder.util import session_scope

    with session_scope(hass=hass, read_only=True) as session:
        metadata_id = (
            session.query(StatisticsMeta.id)
            .filter(StatisticsMeta.statistic_id == entity_id)
            .scalar()
        )
        if metadata_id is None:
            return 0
        return (
            session.query(Statistics).filter(Statistics.metadata_id == metadata_id).count()
        )


def _meta_exists(hass: HomeAssistant, entity_id: str) -> bool:
    """Apakah baris metadata entity itu masih ada."""
    from homeassistant.components.recorder.db_schema import StatisticsMeta
    from homeassistant.components.recorder.util import session_scope

    with session_scope(hass=hass, read_only=True) as session:
        return (
            session.query(StatisticsMeta.id)
            .filter(StatisticsMeta.statistic_id == entity_id)
            .scalar()
            is not None
        )


async def _seed(hass: HomeAssistant) -> None:
    """Isi database dengan data lama dan baru, milik kita dan milik orang lain."""
    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    old = [now - timedelta(days=800 + offset) for offset in range(3)]
    recent = [now - timedelta(days=10 + offset) for offset in range(2)]

    _import_rows(hass, OUR_ENTITY, old + recent)
    # Entity milik integrasi lain, dengan data sama-sama tua.
    _import_rows(hass, FOREIGN_ENTITY, old)
    await async_wait_recording_done(hass)


async def _purge(hass: HomeAssistant, **data) -> dict:
    """Panggil layanan pembersihan."""
    return await hass.services.async_call(
        DOMAIN, SERVICE_PURGE_OLD_DATA, data, blocking=True, return_response=True
    )


async def test_purge_never_touches_other_peoples_data(
    recorder_mock, hass: HomeAssistant
) -> None:
    """Pagar terpenting: hanya data milik integrasi ini yang dihapus (spec N.3).

    Data entity lain dengan umur yang sama persis harus tetap utuh seluruhnya.
    """
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, **{CONF_STATISTICS_RETENTION_YEARS: "1"})
    await _seed(hass)

    assert _count_rows(hass, OUR_ENTITY) == 5
    assert _count_rows(hass, FOREIGN_ENTITY) == 3

    response = await _purge(hass)

    # Tiga baris tua milik kita hilang, dua yang baru tetap ada.
    assert _count_rows(hass, OUR_ENTITY) == 2
    # Milik orang lain tidak berkurang satu baris pun.
    assert _count_rows(hass, FOREIGN_ENTITY) == 3
    assert response["long_term_rows"] == 3


async def test_metadata_row_is_kept(recorder_mock, hass: HomeAssistant) -> None:
    """Baris metadata tidak boleh ikut terhapus.

    Menghapusnya akan memicu ON DELETE CASCADE yang membuang seluruh riwayat
    entity itu - persis yang ingin dihindari (spec N.4).
    """
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, **{CONF_STATISTICS_RETENTION_YEARS: "1"})
    await _seed(hass)

    await _purge(hass)

    assert _meta_exists(hass, OUR_ENTITY) is True
    assert _meta_exists(hass, FOREIGN_ENTITY) is True


async def test_recent_data_survives(recorder_mock, hass: HomeAssistant) -> None:
    """Retensi yang longgar berarti tidak ada yang terhapus."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, **{CONF_STATISTICS_RETENTION_YEARS: "5"})
    await _seed(hass)

    response = await _purge(hass)

    assert response["long_term_rows"] == 0
    assert _count_rows(hass, OUR_ENTITY) == 5


async def test_keep_years_can_be_overridden_per_call(
    recorder_mock, hass: HomeAssistant
) -> None:
    """Retensi bisa ditimpa sekali pakai saat memanggil layanan."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, **{CONF_STATISTICS_RETENTION_YEARS: "5"})
    await _seed(hass)

    response = await _purge(hass, keep_years="1")

    assert response["long_term_rows"] == 3
    assert _count_rows(hass, OUR_ENTITY) == 2


async def test_unlimited_retention_refuses_to_delete(
    recorder_mock, hass: HomeAssistant
) -> None:
    """'Selamanya' berarti benar-benar tidak menghapus, bukan memakai bawaan."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, **{CONF_STATISTICS_RETENTION_YEARS: RETENTION_UNLIMITED})
    await _seed(hass)

    with pytest.raises(ServiceValidationError):
        await _purge(hass)

    assert _count_rows(hass, OUR_ENTITY) == 5


async def test_default_is_never_delete(recorder_mock, hass: HomeAssistant) -> None:
    """Tanpa pengaturan apa pun, tidak ada yang terhapus."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass)
    await _seed(hass)

    with pytest.raises(ServiceValidationError):
        await _purge(hass)

    assert _count_rows(hass, OUR_ENTITY) == 5


async def test_response_summarises_what_happened(
    recorder_mock, hass: HomeAssistant
) -> None:
    """Hasilnya dilaporkan apa adanya, bisa diperiksa user."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, **{CONF_STATISTICS_RETENTION_YEARS: "1"})
    await _seed(hass)

    response = await _purge(hass)

    assert response["entities"] > 0
    assert response["total_rows"] == 3
    assert response["cutoff"]


async def test_unsupported_schema_fails_loudly_and_deletes_nothing(
    recorder_mock, hass: HomeAssistant
) -> None:
    """Kalau struktur recorder berubah, gagal terang-terangan (spec N.4).

    Yang paling berbahaya bukan gagal, melainkan diam-diam menghapus baris yang
    salah. Jadi kegagalan harus terjadi sebelum satu baris pun tersentuh.
    """
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, **{CONF_STATISTICS_RETENTION_YEARS: "1"})
    await _seed(hass)

    with (
        patch(
            "custom_components.pln_prepaid_monitor.retention.check_supported",
            side_effect=RetentionUnsupportedError("kolom start_ts tidak ada"),
        ),
        pytest.raises(ServiceValidationError),
    ):
        await _purge(hass)

    assert _count_rows(hass, OUR_ENTITY) == 5
    assert _count_rows(hass, FOREIGN_ENTITY) == 3


async def test_purge_can_target_one_billing_group(
    recorder_mock, hass: HomeAssistant
) -> None:
    """Menargetkan satu kelompok membatasi pembersihan ke entity kelompok itu."""
    apply_states(hass, MCB_RUMAH)
    await _setup(hass, **{CONF_STATISTICS_RETENTION_YEARS: "1"})
    await _seed(hass)

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, RUMAH_ID)})
    response = await _purge(hass, device_id=[device.id])

    # Perangkat sumber tidak memiliki sensor energi grup, jadi tidak ada yang
    # terhapus - dan data grup tetap utuh.
    assert response["long_term_rows"] == 0
    assert _count_rows(hass, OUR_ENTITY) == 5


async def test_statistic_ids_only_ever_contain_our_entities(
    hass: HomeAssistant,
) -> None:
    """Daftar entity yang boleh dihapus dibangun dari registry, bukan pola nama."""
    from custom_components.pln_prepaid_monitor.services import our_statistic_ids

    apply_states(hass, MCB_RUMAH)
    hass.states.async_set(FOREIGN_ENTITY, "123", {})
    await _setup(hass)

    ids = our_statistic_ids(hass)

    assert ids
    assert FOREIGN_ENTITY not in ids
    assert all(entity_id.startswith(("sensor.", "binary_sensor.")) for entity_id in ids)


async def test_auto_purge_is_off_by_default(hass: HomeAssistant) -> None:
    """Pembersihan otomatis tidak pernah menyala tanpa diminta."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)

    assert entry.runtime_data._auto_purge_unsub is None


async def test_auto_purge_can_be_enabled(hass: HomeAssistant) -> None:
    """Kalau dinyalakan user, jadwal hariannya terpasang."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(
        hass,
        **{
            CONF_AUTO_PURGE_ENABLED: True,
            CONF_STATISTICS_RETENTION_YEARS: "2",
        },
    )

    assert entry.runtime_data._auto_purge_unsub is not None


# --- pengaturan lewat options flow -------------------------------------------


async def test_retention_can_be_set_in_the_options_flow(
    hass: HomeAssistant,
) -> None:
    """Pengaturan perawatan data tersimpan lewat tombol Configure."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_STATISTICS_RETENTION_YEARS: "3",
            CONF_AUTO_PURGE_ENABLED: True,
        },
    )
    await hass.async_block_till_done()

    assert entry.options[CONF_STATISTICS_RETENTION_YEARS] == "3"
    assert entry.options[CONF_AUTO_PURGE_ENABLED] is True
