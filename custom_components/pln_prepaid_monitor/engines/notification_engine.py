"""Kapan notifikasi token boleh dikirim, dan kapan sebaiknya diam.

*Pure Python*, supaya seluruh aturan anti-spam bisa diuji sebagai fungsi biasa
tanpa mengirim satu pun pesan sungguhan.

Aturan yang dipegang (spec I.2):

* **Dikirim saat berpindah tingkat**, bukan berulang-ulang selama keadaannya
  sama. Peringatan yang datang tiap lima menit akan berhenti dibaca orang.
* **Jam tenang** dihormati - kecuali keadaan sangat kritis, kalau user
  mengizinkannya menembus.
* Notifikasi yang tertahan jam tenang **tidak hilang**: begitu jam tenang
  lewat, ia tetap dikirim, karena tingkatnya belum pernah tercatat terkirim.
* Ada **pesan pemulihan** sekali saat token terisi lagi, supaya user tahu
  keadaan sudah aman - bukan dibiarkan menebak.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

from .prediction_engine import (
    STATUS_HOLD,
    STATUS_NORMAL,
    STATUS_UNKNOWN,
    STATUS_VERY_CRITICAL,
)

KIND_ALERT = "alert"
KIND_RECOVERY = "recovery"
KIND_HOLD = "hold"

REASON_DISABLED = "disabled"
REASON_UNKNOWN_STATUS = "unknown_status"
REASON_NO_CHANGE = "no_change"
REASON_ALREADY_NORMAL = "already_normal"
REASON_QUIET_HOURS = "quiet_hours"
REASON_COOLDOWN = "cooldown"
REASON_RECOVERY_DISABLED = "recovery_disabled"
REASON_SEND = "send"

DEFAULT_MESSAGE_PREFIX = "[Token PLN]"


def _parse_time(value: Any) -> time | None:
    """Baca jam dari TimeSelector, yang berbentuk teks 'HH:MM:SS'."""
    if isinstance(value, time):
        return value
    if not value:
        return None
    parts = str(value).split(":")
    try:
        return time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except (ValueError, IndexError):
        return None


@dataclass(frozen=True)
class NotificationConfig:
    """Pengaturan pengiriman notifikasi untuk satu kelompok tagihan."""

    enabled: bool = False
    targets: tuple[str, ...] = ()
    create_persistent_notification: bool = True
    message_prefix: str = DEFAULT_MESSAGE_PREFIX
    cooldown_hours: float = 12.0
    repeat_while_unresolved: bool = False
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    bypass_quiet_hours_for_very_critical: bool = True
    notify_on_recovery: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> NotificationConfig:
        """Baca dari konfigurasi subentry, toleran terhadap isian yang aneh."""
        data = data or {}
        try:
            cooldown = float(data.get("cooldown_hours", 12.0))
        except (TypeError, ValueError):
            cooldown = 12.0

        targets = data.get("notify_targets") or []
        if isinstance(targets, str):
            targets = [targets]

        prefix = str(
            data.get("message_prefix", DEFAULT_MESSAGE_PREFIX)
            or DEFAULT_MESSAGE_PREFIX
        )

        return cls(
            enabled=bool(data.get("notify_enabled", False)),
            targets=tuple(str(target) for target in targets),
            create_persistent_notification=bool(
                data.get("create_persistent_notification", True)
            ),
            message_prefix=prefix,
            cooldown_hours=max(0.0, cooldown),
            repeat_while_unresolved=bool(data.get("repeat_while_unresolved", False)),
            quiet_hours_start=_parse_time(data.get("quiet_hours_start")),
            quiet_hours_end=_parse_time(data.get("quiet_hours_end")),
            bypass_quiet_hours_for_very_critical=bool(
                data.get("bypass_quiet_hours_for_very_critical", True)
            ),
            notify_on_recovery=bool(data.get("notify_on_recovery", True)),
        )

    def in_quiet_hours(self, now: datetime) -> bool:
        """Apakah saat ini termasuk jam tenang."""
        start = self.quiet_hours_start
        end = self.quiet_hours_end
        if start is None or end is None or start == end:
            return False

        current = now.time()
        if start < end:
            return start <= current < end
        # Melewati tengah malam, misalnya 22:00 sampai 06:00.
        return current >= start or current < end


@dataclass
class NotifierState:
    """Apa yang sudah pernah dikirim, wajib bertahan lintas restart."""

    last_notified_level: str | None = None
    last_sent_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Bentuk yang disimpan ke .storage."""
        return {
            "last_notified_level": self.last_notified_level,
            "last_sent_at": self.last_sent_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> NotifierState:
        """Baca kembali dari .storage."""
        if not data:
            return cls()
        return cls(
            last_notified_level=data.get("last_notified_level"),
            last_sent_at=data.get("last_sent_at"),
        )

    def elapsed_hours(self, now: datetime) -> float | None:
        """Berapa jam sejak pesan terakhir dikirim."""
        if not self.last_sent_at:
            return None
        try:
            sent = datetime.fromisoformat(self.last_sent_at)
        except ValueError:
            return None
        return (now - sent) / timedelta(hours=1)

    def record_sent(self, level: str, now: datetime) -> None:
        """Catat bahwa satu pesan untuk tingkat ini sudah terkirim."""
        self.last_notified_level = level
        self.last_sent_at = now.isoformat()


@dataclass(frozen=True)
class NotificationDecision:
    """Hasil pertimbangan: kirim atau tidak, dan kenapa."""

    send: bool
    reason: str
    kind: str | None = None
    level: str | None = None


def decide(
    *,
    status: str,
    state: NotifierState,
    config: NotificationConfig,
    now: datetime,
) -> NotificationDecision:
    """Tentukan apakah satu notifikasi pantas dikirim sekarang."""
    if not config.enabled:
        return NotificationDecision(False, REASON_DISABLED)

    if status == STATUS_UNKNOWN:
        # Data belum cukup untuk menyimpulkan apa pun - tidak ada yang perlu
        # diberitahukan, dan mengirim pesan justru membingungkan.
        return NotificationDecision(False, REASON_UNKNOWN_STATUS)

    last = state.last_notified_level

    if status == STATUS_NORMAL:
        if last in (None, STATUS_NORMAL):
            return NotificationDecision(False, REASON_ALREADY_NORMAL)
        if not config.notify_on_recovery:
            return NotificationDecision(False, REASON_RECOVERY_DISABLED)
        return _apply_quiet_hours(
            NotificationDecision(True, REASON_SEND, KIND_RECOVERY, status),
            config=config,
            now=now,
            status=status,
        )

    kind = KIND_HOLD if status == STATUS_HOLD else KIND_ALERT

    if status != last:
        # Berpindah tingkat: inilah momen yang memang layak diberitahukan.
        return _apply_quiet_hours(
            NotificationDecision(True, REASON_SEND, kind, status),
            config=config,
            now=now,
            status=status,
        )

    if not config.repeat_while_unresolved:
        return NotificationDecision(False, REASON_NO_CHANGE)

    elapsed = state.elapsed_hours(now)
    if elapsed is not None and elapsed < config.cooldown_hours:
        return NotificationDecision(False, REASON_COOLDOWN)

    return _apply_quiet_hours(
        NotificationDecision(True, REASON_SEND, kind, status),
        config=config,
        now=now,
        status=status,
    )


def _apply_quiet_hours(
    decision: NotificationDecision,
    *,
    config: NotificationConfig,
    now: datetime,
    status: str,
) -> NotificationDecision:
    """Tahan pesan di jam tenang, kecuali keadaan benar-benar darurat.

    Pesan yang tertahan tidak dibuang: karena tingkatnya belum tercatat
    terkirim, ia akan dikirim sendiri begitu jam tenang lewat.
    """
    if not config.in_quiet_hours(now):
        return decision
    if status == STATUS_VERY_CRITICAL and config.bypass_quiet_hours_for_very_critical:
        return decision
    return NotificationDecision(False, REASON_QUIET_HOURS)
