"""Test aturan anti-spam notifikasi - tanpa mengirim satu pun pesan sungguhan."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.pln_prepaid_monitor.engines.notification_engine import (
    KIND_ALERT,
    KIND_HOLD,
    KIND_RECOVERY,
    REASON_ALREADY_NORMAL,
    REASON_COOLDOWN,
    REASON_DISABLED,
    REASON_NO_CHANGE,
    REASON_QUIET_HOURS,
    REASON_RECOVERY_DISABLED,
    REASON_UNKNOWN_STATUS,
    NotificationConfig,
    NotifierState,
    decide,
)
from custom_components.pln_prepaid_monitor.engines.prediction_engine import (
    STATUS_CRITICAL,
    STATUS_HOLD,
    STATUS_NORMAL,
    STATUS_UNKNOWN,
    STATUS_VERY_CRITICAL,
    STATUS_WARNING,
)

JAKARTA = ZoneInfo("Asia/Jakarta")


def _at(text: str) -> datetime:
    """Waktu lokal Jakarta dari string."""
    return datetime.fromisoformat(text).replace(tzinfo=JAKARTA)


NOON = _at("2026-09-03 12:00:00")


def _config(**overrides) -> NotificationConfig:
    """Konfigurasi notifikasi yang aktif, dengan penyesuaian seperlunya."""
    defaults = {"enabled": True}
    defaults.update(overrides)
    return NotificationConfig(**defaults)


def _decide(status, state=None, config=None, now=NOON):
    """Panggil decide dengan bawaan yang ringkas."""
    return decide(
        status=status,
        state=state or NotifierState(),
        config=config or _config(),
        now=now,
    )


# --- dasar -------------------------------------------------------------------


def test_disabled_never_sends() -> None:
    """Notifikasi mati berarti benar-benar diam."""
    decision = _decide(STATUS_VERY_CRITICAL, config=_config(enabled=False))
    assert decision.send is False
    assert decision.reason == REASON_DISABLED


def test_unknown_status_does_not_nag() -> None:
    """Selama data belum cukup, tidak ada yang layak diberitahukan."""
    decision = _decide(STATUS_UNKNOWN)
    assert decision.send is False
    assert decision.reason == REASON_UNKNOWN_STATUS


def test_first_warning_is_sent() -> None:
    """Berpindah dari belum-pernah ke perlu-perhatian: dikirim."""
    decision = _decide(STATUS_WARNING)
    assert decision.send is True
    assert decision.kind == KIND_ALERT
    assert decision.level == STATUS_WARNING


def test_same_level_is_not_repeated() -> None:
    """Selama tingkatnya sama, sistem diam - ini inti aturan anti-spam."""
    state = NotifierState(last_notified_level=STATUS_WARNING)
    decision = _decide(STATUS_WARNING, state=state)
    assert decision.send is False
    assert decision.reason == REASON_NO_CHANGE


def test_escalation_is_sent() -> None:
    """Naik tingkat dari perlu-perhatian ke kritis: dikirim lagi."""
    state = NotifierState(last_notified_level=STATUS_WARNING)
    decision = _decide(STATUS_CRITICAL, state=state)
    assert decision.send is True
    assert decision.level == STATUS_CRITICAL


def test_de_escalation_is_also_sent() -> None:
    """Turun tingkat juga perubahan yang layak diketahui user."""
    state = NotifierState(last_notified_level=STATUS_CRITICAL)
    decision = _decide(STATUS_WARNING, state=state)
    assert decision.send is True


# --- pengulangan dan cooldown ------------------------------------------------


def test_repeat_waits_for_the_cooldown() -> None:
    """Pengulangan tidak boleh lebih rapat dari jarak minimum."""
    state = NotifierState(
        last_notified_level=STATUS_CRITICAL,
        last_sent_at=_at("2026-09-03 09:00:00").isoformat(),
    )
    config = _config(repeat_while_unresolved=True, cooldown_hours=12)

    # Baru 3 jam berlalu.
    assert _decide(STATUS_CRITICAL, state=state, config=config).reason == (
        REASON_COOLDOWN
    )

    # Sudah lewat 12 jam.
    later = _at("2026-09-03 21:30:00")
    assert _decide(STATUS_CRITICAL, state=state, config=config, now=later).send is True


def test_no_repeat_without_the_option() -> None:
    """Tanpa mengaktifkan pengulangan, satu tingkat cukup sekali diberitahukan."""
    state = NotifierState(
        last_notified_level=STATUS_CRITICAL,
        last_sent_at=_at("2026-09-01 09:00:00").isoformat(),
    )
    decision = _decide(STATUS_CRITICAL, state=state)
    assert decision.send is False
    assert decision.reason == REASON_NO_CHANGE


# --- jam tenang --------------------------------------------------------------


def test_quiet_hours_hold_ordinary_warnings() -> None:
    """Peringatan biasa ditahan selama jam tenang."""
    config = _config(
        quiet_hours_start=time(22, 0), quiet_hours_end=time(6, 0)
    )
    decision = _decide(STATUS_WARNING, config=config, now=_at("2026-09-03 23:30:00"))
    assert decision.send is False
    assert decision.reason == REASON_QUIET_HOURS


def test_very_critical_breaks_through_quiet_hours() -> None:
    """Keadaan darurat tetap sampai, kalau user mengizinkannya."""
    config = _config(
        quiet_hours_start=time(22, 0),
        quiet_hours_end=time(6, 0),
        bypass_quiet_hours_for_very_critical=True,
    )
    decision = _decide(
        STATUS_VERY_CRITICAL, config=config, now=_at("2026-09-03 23:30:00")
    )
    assert decision.send is True


def test_very_critical_can_also_be_silenced() -> None:
    """Kalau user mematikan penembusan, jam tenang tetap dihormati."""
    config = _config(
        quiet_hours_start=time(22, 0),
        quiet_hours_end=time(6, 0),
        bypass_quiet_hours_for_very_critical=False,
    )
    decision = _decide(
        STATUS_VERY_CRITICAL, config=config, now=_at("2026-09-03 23:30:00")
    )
    assert decision.send is False


def test_held_message_is_sent_once_quiet_hours_end() -> None:
    """Pesan yang tertahan tidak hilang - hanya tertunda.

    Karena tingkatnya tidak pernah tercatat terkirim, ia otomatis dikirim pada
    evaluasi berikutnya begitu jam tenang lewat.
    """
    config = _config(quiet_hours_start=time(22, 0), quiet_hours_end=time(6, 0))
    state = NotifierState()

    assert (
        _decide(
            STATUS_WARNING, state=state, config=config, now=_at("2026-09-03 23:30:00")
        ).send
        is False
    )
    assert state.last_notified_level is None

    assert (
        _decide(
            STATUS_WARNING, state=state, config=config, now=_at("2026-09-04 07:00:00")
        ).send
        is True
    )


@pytest.mark.parametrize(
    ("moment", "quiet"),
    [
        ("2026-09-03 21:59:00", False),
        ("2026-09-03 22:00:00", True),
        ("2026-09-04 03:00:00", True),
        ("2026-09-04 05:59:00", True),
        ("2026-09-04 06:00:00", False),
    ],
)
def test_quiet_hours_wrap_around_midnight(moment: str, quiet: bool) -> None:
    """Jam tenang 22:00-06:00 melewati tengah malam dengan benar."""
    config = _config(quiet_hours_start=time(22, 0), quiet_hours_end=time(6, 0))
    assert config.in_quiet_hours(_at(moment)) is quiet


def test_no_quiet_hours_when_unset() -> None:
    """Tanpa jam tenang, tidak ada yang ditahan kapan pun."""
    assert _config().in_quiet_hours(_at("2026-09-04 03:00:00")) is False


# --- pemulihan ---------------------------------------------------------------


def test_recovery_message_after_topup() -> None:
    """Setelah token terisi, satu pesan penutup dikirim."""
    state = NotifierState(last_notified_level=STATUS_CRITICAL)
    decision = _decide(STATUS_NORMAL, state=state)
    assert decision.send is True
    assert decision.kind == KIND_RECOVERY


def test_no_recovery_message_when_never_warned() -> None:
    """Kalau tidak pernah ada peringatan, tidak perlu ada pesan 'sudah aman'."""
    decision = _decide(STATUS_NORMAL)
    assert decision.send is False
    assert decision.reason == REASON_ALREADY_NORMAL


def test_recovery_can_be_switched_off() -> None:
    """User boleh memilih tidak menerima pesan pemulihan."""
    state = NotifierState(last_notified_level=STATUS_CRITICAL)
    decision = _decide(
        STATUS_NORMAL, state=state, config=_config(notify_on_recovery=False)
    )
    assert decision.send is False
    assert decision.reason == REASON_RECOVERY_DISABLED


def test_normal_is_not_repeated() -> None:
    """Status aman yang sudah dilaporkan tidak diulang-ulang."""
    state = NotifierState(last_notified_level=STATUS_NORMAL)
    assert _decide(STATUS_NORMAL, state=state).send is False


# --- penahanan ledger --------------------------------------------------------


def test_hold_is_announced_once() -> None:
    """Penahanan ledger diberitahukan sekali, dengan jenis pesannya sendiri."""
    decision = _decide(STATUS_HOLD)
    assert decision.send is True
    assert decision.kind == KIND_HOLD

    state = NotifierState(last_notified_level=STATUS_HOLD)
    assert _decide(STATUS_HOLD, state=state).send is False


# --- state -------------------------------------------------------------------


def test_state_survives_restart() -> None:
    """Apa yang sudah dikirim tidak boleh terlupa setelah restart."""
    state = NotifierState()
    state.record_sent(STATUS_WARNING, NOON)

    restored = NotifierState.from_dict(state.as_dict())
    assert restored.last_notified_level == STATUS_WARNING
    assert _decide(STATUS_WARNING, state=restored).send is False


def test_elapsed_hours_handles_missing_and_broken_values() -> None:
    """Data waktu yang hilang atau rusak tidak boleh membuat integrasi gagal."""
    assert NotifierState().elapsed_hours(NOON) is None
    assert NotifierState(last_sent_at="bukan waktu").elapsed_hours(NOON) is None
    state = NotifierState(last_sent_at=(NOON - timedelta(hours=5)).isoformat())
    assert state.elapsed_hours(NOON) == pytest.approx(5.0)


def test_config_from_dict_uses_safe_defaults() -> None:
    """Isian rusak jatuh ke nilai bawaan yang aman."""
    config = NotificationConfig.from_dict(
        {
            "notify_enabled": True,
            "cooldown_hours": "bukan angka",
            "quiet_hours_start": "aneh",
            "notify_targets": "notify.telegram",
        }
    )
    assert config.cooldown_hours == pytest.approx(12.0)
    assert config.quiet_hours_start is None
    # Satu target berbentuk teks tetap diterima sebagai satu daftar.
    assert config.targets == ("notify.telegram",)


def test_default_prefix_marks_our_messages() -> None:
    """Awalan bawaan membedakan pesan kita dari automation lain (spec O.5)."""
    assert NotificationConfig.from_dict({}).message_prefix == "[Token PLN]"
