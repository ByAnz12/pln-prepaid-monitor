"""Test pengiriman notifikasi token pada instance Home Assistant sungguhan."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.components import persistent_notification as pn
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pln_prepaid_monitor.const import (
    CONF_BYPASS_QUIET_HOURS,
    CONF_CREATE_PERSISTENT_NOTIFICATION,
    CONF_CRITICAL_THRESHOLD_DAYS,
    CONF_CYCLE_PERIODS,
    CONF_ENERGY_ENTITY_ID,
    CONF_FIXED_CHARGE_PERIOD,
    CONF_FIXED_CHARGE_RP,
    CONF_MESSAGE_PREFIX,
    CONF_NOTIFY_ENABLED,
    CONF_NOTIFY_ON_RECOVERY,
    CONF_NOTIFY_TARGETS,
    CONF_QUIET_HOURS_END,
    CONF_QUIET_HOURS_START,
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
    SERVICE_RESOLVE_LEDGER_HOLD,
    SUBENTRY_TYPE_BILLING_GROUP,
    SUBENTRY_TYPE_ENERGY_SOURCE,
    SUBENTRY_TYPE_TARIFF,
)

from .conftest import apply_states, MCB_RUMAH

RUMAH_ID = "src_rumah"
TARIFF_ID = "tar_r1"
GROUP_ID = "grp_rumah"

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


def _group(**overrides) -> dict:
    """Kelompok tagihan dengan token dan notifikasi aktif."""
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
        CONF_NOTIFY_ENABLED: True,
        CONF_NOTIFY_TARGETS: ["notify.telegram_keluarga"],
        CONF_CREATE_PERSISTENT_NOTIFICATION: True,
        CONF_MESSAGE_PREFIX: "[Token PLN]",
        CONF_NOTIFY_ON_RECOVERY: True,
        CONF_BYPASS_QUIET_HOURS: True,
    }
    data.update(overrides)
    return {
        "data": data,
        "subentry_id": GROUP_ID,
        "subentry_type": SUBENTRY_TYPE_BILLING_GROUP,
        "title": "PLN RUMAH",
        "unique_id": None,
    }


@pytest.fixture
def sent_messages(hass: HomeAssistant) -> list[dict]:
    """Tangkap pesan yang dikirim ke service notify palsu."""
    messages: list[dict] = []

    async def _capture(call: ServiceCall) -> None:
        messages.append(dict(call.data))

    hass.services.async_register("notify", "telegram_keluarga", _capture)
    return messages


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


def _fake_samples(**windows):
    """Ganti pembacaan statistik dengan sampel yang sudah ditentukan."""

    async def _fetch(hass, statistic_id, config, now):
        return dict(windows)

    return patch(
        "custom_components.pln_prepaid_monitor.coordinator."
        "async_fetch_window_samples",
        new=_fetch,
    )


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Paksa evaluasi ulang prediksi dan notifikasi."""
    with _fake_samples(**{"7d": [10.0] * 7}):
        await entry.runtime_data.billing_groups[GROUP_ID].async_refresh_prediction()
    await hass.async_block_till_done()


async def test_warning_is_sent_with_prefix(
    hass: HomeAssistant, sent_messages: list[dict]
) -> None:
    """Peringatan pertama terkirim, dengan awalan yang membedakannya."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    await _topup(hass, 50.0)  # 50 kWh / 10 per hari = 5 hari -> perlu perhatian
    await _refresh(hass, entry)

    assert len(sent_messages) == 1
    message = sent_messages[0]
    # Awalan ini yang membedakan dari automation PLN padam/nyala (spec O.5).
    assert message["title"].startswith("[Token PLN]")
    assert "PLN RUMAH" in message["title"]
    assert "50" in message["message"]
    assert "kWh" in message["message"]


async def test_same_level_is_not_repeated(
    hass: HomeAssistant, sent_messages: list[dict]
) -> None:
    """Evaluasi berulang pada tingkat yang sama tidak menghasilkan pesan baru."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    await _topup(hass, 50.0)
    await _refresh(hass, entry)
    await _refresh(hass, entry)
    await _refresh(hass, entry)

    assert len(sent_messages) == 1


async def test_escalation_sends_a_second_message(
    hass: HomeAssistant, sent_messages: list[dict]
) -> None:
    """Naik dari perlu-perhatian ke kritis memicu pesan baru."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    await _topup(hass, 50.0)
    await _refresh(hass, entry)
    assert len(sent_messages) == 1

    # Pakai listrik sampai sisa 20 kWh -> 2 hari -> kritis.
    hass.states.async_set(
        "sensor.mcb_rumah_total_energy",
        "15528.27",
        MCB_RUMAH["sensor.mcb_rumah_total_energy"][1],
    )
    await hass.async_block_till_done()
    await _refresh(hass, entry)

    assert len(sent_messages) == 2


async def test_recovery_message_after_topup(
    hass: HomeAssistant, sent_messages: list[dict]
) -> None:
    """Setelah token diisi lagi, ada satu pesan penutup."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    await _topup(hass, 50.0)
    await _refresh(hass, entry)
    assert len(sent_messages) == 1

    await _topup(hass, 300.0)  # sekarang aman
    await _refresh(hass, entry)

    assert len(sent_messages) == 2
    assert "topped up" in sent_messages[1]["title"].lower()


async def test_quiet_hours_hold_ordinary_warning(
    hass: HomeAssistant, sent_messages: list[dict], freezer
) -> None:
    """Peringatan biasa ditahan di jam tenang, lalu dikirim setelah lewat."""
    await hass.config.async_set_time_zone("Asia/Jakarta")
    freezer.move_to("2026-09-03 16:30:00+00:00")  # 23:30 WIB
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(
        hass,
        SOURCE_SUBENTRY,
        TARIFF_SUBENTRY,
        _group(
            **{
                CONF_QUIET_HOURS_START: "22:00:00",
                CONF_QUIET_HOURS_END: "06:00:00",
            }
        ),
    )

    await _topup(hass, 50.0)
    await _refresh(hass, entry)
    assert sent_messages == []

    # Pagi harinya, pesan yang tertahan tetap dikirim.
    freezer.move_to("2026-09-04 01:00:00+00:00")  # 08:00 WIB
    await _refresh(hass, entry)
    assert len(sent_messages) == 1


async def test_very_critical_breaks_through_quiet_hours(
    hass: HomeAssistant, sent_messages: list[dict], freezer
) -> None:
    """Keadaan hampir padam tetap membangunkan user, kalau diizinkan."""
    await hass.config.async_set_time_zone("Asia/Jakarta")
    freezer.move_to("2026-09-03 16:30:00+00:00")  # 23:30 WIB
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(
        hass,
        SOURCE_SUBENTRY,
        TARIFF_SUBENTRY,
        _group(
            **{
                CONF_QUIET_HOURS_START: "22:00:00",
                CONF_QUIET_HOURS_END: "06:00:00",
                CONF_BYPASS_QUIET_HOURS: True,
            }
        ),
    )

    await _topup(hass, 5.0)  # 5 kWh / 10 per hari = 0,5 hari -> sangat kritis
    await _refresh(hass, entry)

    assert len(sent_messages) == 1
    assert "VERY CRITICAL" in sent_messages[0]["title"]


async def test_notifications_can_be_disabled(
    hass: HomeAssistant, sent_messages: list[dict]
) -> None:
    """Kelompok tanpa notifikasi tidak mengirim apa pun."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(
        hass,
        SOURCE_SUBENTRY,
        TARIFF_SUBENTRY,
        _group(**{CONF_NOTIFY_ENABLED: False}),
    )

    await _topup(hass, 50.0)
    await _refresh(hass, entry)

    assert sent_messages == []


async def test_persistent_notification_is_created(hass: HomeAssistant) -> None:
    """Pesan juga muncul di lonceng notifikasi Home Assistant."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(
        hass,
        SOURCE_SUBENTRY,
        TARIFF_SUBENTRY,
        _group(**{CONF_NOTIFY_TARGETS: []}),
    )

    await _topup(hass, 50.0)
    await _refresh(hass, entry)

    notifications = pn._async_get_or_create_notifications(hass)
    assert f"{DOMAIN}_{GROUP_ID}" in notifications


async def test_state_survives_reload(
    hass: HomeAssistant, sent_messages: list[dict]
) -> None:
    """Setelah restart, peringatan yang sama tidak dikirim ulang."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())

    await _topup(hass, 50.0)
    await _refresh(hass, entry)
    assert len(sent_messages) == 1

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await _refresh(hass, entry)

    assert len(sent_messages) == 1


# --- kartu Repairs untuk penahanan ledger ------------------------------------


async def test_ledger_hold_creates_a_repairs_card(
    hass: HomeAssistant, sent_messages: list[dict]
) -> None:
    """Penahanan ledger muncul sebagai kartu Repairs sekaligus pesan."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())
    await _topup(hass, 50.0)
    await _refresh(hass, entry)
    sent_messages.clear()

    # Meteran diganti: pembacaan melompat ke angka besar.
    hass.states.async_set(
        "sensor.mcb_rumah_total_energy",
        "9000",
        MCB_RUMAH["sensor.mcb_rumah_total_energy"][1],
    )
    await hass.async_block_till_done()
    await _refresh(hass, entry)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"ledger_hold_{GROUP_ID}")
    assert issue is not None
    assert issue.translation_placeholders["group"] == "PLN RUMAH"
    assert issue.translation_placeholders["source"] == "MCB RUMAH"

    assert len(sent_messages) == 1
    assert "on hold" in sent_messages[0]["title"].lower()
    assert "MCB RUMAH" in sent_messages[0]["message"]


async def test_repairs_card_disappears_after_resolution(
    hass: HomeAssistant, sent_messages: list[dict]
) -> None:
    """Begitu user memutuskan, kartu Repairs-nya hilang sendiri."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(hass, SOURCE_SUBENTRY, TARIFF_SUBENTRY, _group())
    await _topup(hass, 50.0)
    hass.states.async_set(
        "sensor.mcb_rumah_total_energy",
        "9000",
        MCB_RUMAH["sensor.mcb_rumah_total_energy"][1],
    )
    await hass.async_block_till_done()
    await _refresh(hass, entry)
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, f"ledger_hold_{GROUP_ID}")
        is not None
    )

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, GROUP_ID)})
    await hass.services.async_call(
        DOMAIN,
        SERVICE_RESOLVE_LEDGER_HOLD,
        {"device_id": [device.id], "action": "ignore"},
        blocking=True,
    )
    await hass.async_block_till_done()
    await _refresh(hass, entry)

    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, f"ledger_hold_{GROUP_ID}") is None
    )


async def test_broken_notify_target_does_not_crash(
    hass: HomeAssistant,
) -> None:
    """Target notifikasi yang tidak ada dicatat, bukan menjatuhkan integrasi."""
    apply_states(hass, MCB_RUMAH)
    entry = await _setup(
        hass,
        SOURCE_SUBENTRY,
        TARIFF_SUBENTRY,
        _group(**{CONF_NOTIFY_TARGETS: ["notify.tidak_ada"]}),
    )

    await _topup(hass, 50.0)
    await _refresh(hass, entry)

    # Sensor tetap hidup dan status tetap terhitung.
    assert hass.states.get("sensor.pln_rumah_token_status").state == "warning"
