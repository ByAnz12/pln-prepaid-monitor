"""Pengiriman notifikasi token, dengan pagar pengaman yang keras.

Ini **satu-satunya** tempat di seluruh integrasi yang memanggil service Home
Assistant. Aturannya dikunci di sini, bukan sekadar dipercayakan pada
kedisiplinan penulis kode berikutnya:

* Hanya domain di :data:`ALLOWED_SERVICE_DOMAINS` yang boleh dipanggil.
* Domain apa pun di luar itu - terutama ``switch`` - menghasilkan
  :class:`ForbiddenServiceError`, bukan panggilan yang terlanjur jalan.

Dengan begitu, kemampuan mengirim pesan bisa ditambahkan tanpa melonggarkan
aturan bahwa sistem ini tidak boleh mengendalikan listrik.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .engines.notification_engine import (
    KIND_HOLD,
    KIND_RECOVERY,
    NotificationConfig,
    decide,
)
from .messages import notification_text, pick_language, test_notification_text

if TYPE_CHECKING:
    from .coordinator import BillingGroupRuntime

_LOGGER = logging.getLogger(__name__)

# Satu-satunya domain service yang boleh dipanggil integrasi ini.
ALLOWED_SERVICE_DOMAINS = frozenset({"notify"})

HOLD_ISSUE_PREFIX = "ledger_hold_"


class ForbiddenServiceError(RuntimeError):
    """Dilempar saat ada upaya memanggil service di luar daftar yang diizinkan."""


async def async_call_notify_target(
    hass: HomeAssistant, target: str, message: str, title: str
) -> None:
    """Kirim satu pesan lewat service notify, setelah domainnya diperiksa.

    :raises ForbiddenServiceError: bila targetnya bukan service ``notify``.
    """
    domain, separator, service = target.partition(".")
    if not separator:
        domain, service = "notify", target

    if domain not in ALLOWED_SERVICE_DOMAINS:
        raise ForbiddenServiceError(
            f"Integrasi ini hanya boleh memanggil service {sorted(ALLOWED_SERVICE_DOMAINS)}, "
            f"bukan '{domain}.{service}'"
        )

    await hass.services.async_call(
        domain, service, {"message": message, "title": title}, blocking=False
    )


class TokenNotifier:
    """Memutuskan dan mengirim notifikasi untuk satu kelompok tagihan."""

    def __init__(self, hass: HomeAssistant, group: BillingGroupRuntime) -> None:
        """Ikat notifier ke satu kelompok tagihan."""
        self.hass = hass
        self.group = group

    @property
    def config(self) -> NotificationConfig:
        """Pengaturan notifikasi kelompok ini."""
        return self.group.notification_config

    async def async_evaluate(self) -> str:
        """Timbang keadaan sekarang, kirim bila pantas. Kembalikan alasannya."""
        status = self.group.token_status
        now = dt_util.now()
        decision = decide(
            status=status,
            state=self.group.notifier_state,
            config=self.config,
            now=now,
        )

        await self._async_sync_hold_issue()

        if not decision.send:
            _LOGGER.debug(
                "Notifikasi '%s' tidak dikirim: %s", self.group.name, decision.reason
            )
            return decision.reason

        title, message = self._build_message(decision.kind, status)
        await self._async_deliver(title, message)
        self.group.notifier_state.record_sent(status, now)
        return decision.reason

    def _build_message(self, kind: str | None, status: str) -> tuple[str, str]:
        """Rakit judul dan isi pesan dalam bahasa Home Assistant user."""
        language = pick_language(self.hass.config.language)
        group = self.group
        return notification_text(
            language,
            kind=kind or KIND_RECOVERY,
            status=status,
            prefix=self.config.message_prefix,
            group_name=group.name,
            remaining_kwh=group.token_remaining_kwh,
            days_remaining=group.prediction.days_remaining,
            empty_date=group.prediction.empty_date,
            remaining_value_rp=group.token_remaining_value_rp,
            hold=group.ledger.state.hold,
        )

    async def async_send_test(self) -> None:
        """Kirim satu pesan percobaan lewat jalur pengiriman yang sama persis.

        Sengaja memakai ``_async_deliver`` yang sama, bukan jalur pintas
        tersendiri: uji coba yang menempuh jalur berbeda tidak membuktikan
        apa-apa tentang notifikasi yang sebenarnya.

        Yang dilewati hanya penimbangan kapan pantas mengirim - tidak ada
        cooldown, tidak ada jam tenang, tidak perlu status token menipis. Itu
        memang gunanya: dipakai saat memeriksa, bukan saat token menipis.
        """
        language = pick_language(self.hass.config.language)
        title, message = test_notification_text(
            language, prefix=self.config.message_prefix, group_name=self.group.name
        )
        _LOGGER.info("Mengirim notifikasi percobaan untuk '%s'", self.group.name)
        await self._async_deliver(title, message)

    async def _async_deliver(self, title: str, message: str) -> None:
        """Kirim ke semua tujuan yang dipilih user."""
        if self.config.create_persistent_notification:
            persistent_notification.async_create(
                self.hass,
                message,
                title=title,
                notification_id=f"{DOMAIN}_{self.group.subentry_id}",
            )

        for target in self.config.targets:
            try:
                await async_call_notify_target(self.hass, target, message, title)
            except ForbiddenServiceError:
                # Tidak pernah boleh terjadi lewat UI, tapi kalau konfigurasi
                # ter-edit manual, lebih baik gagal keras dan tercatat.
                _LOGGER.exception(
                    "Target notifikasi '%s' ditolak oleh pagar pengaman", target
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Gagal mengirim notifikasi lewat '%s' untuk '%s'",
                    target,
                    self.group.name,
                )

    async def _async_sync_hold_issue(self) -> None:
        """Tampilkan penahanan ledger sebagai kartu Repairs, dan cabut saat lepas."""
        issue_id = f"{HOLD_ISSUE_PREFIX}{self.group.subentry_id}"

        if not self.group.ledger.on_hold:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return

        hold: dict[str, Any] = self.group.ledger.state.hold or {}
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="ledger_hold",
            translation_placeholders={
                "group": self.group.name,
                "source": str(hold.get("source_name", "")),
                "reset_to": f"{float(hold.get('reset_to') or 0):.2f}",
            },
        )
