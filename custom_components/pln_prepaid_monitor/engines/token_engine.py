"""Buku besar (ledger) token listrik prabayar.

*Pure Python*, supaya seluruh aritmetika ledger bisa diuji sebagai fungsi biasa.

Model dasarnya (spec G.1)::

    sisa_kwh = total_diisi_kwh - terpakai_sejak_titik_awal

**Bersifat additive** (spec G.2, dikonfirmasi user lewat cek langsung ke layar
meteran): mengisi token baru sebelum yang lama habis akan *menambah* ke sisa
lama, bukan menggantikannya.

Dua hal yang sengaja dibuat begini:

1. **``total_diisi_kwh`` selalu dihitung ulang dari seluruh riwayat**, tidak
   pernah disimpan sebagai satu angka. Jadi kalau user salah ketik lalu
   memperbaikinya lewat ``edit_topup``/``delete_topup``, angka totalnya otomatis
   ikut benar - tidak ada cache yang bisa ketinggalan (spec K.7).
2. **``terpakai_sejak_titik_awal`` dihitung dari total energi kelompok**, yang
   sudah bersifat hanya-naik dan aman terhadap reset counter di lapisan bawah.
   Jadi pengurangan dua titik di sini aman - berbeda dari draft awal spec yang
   mengurangkan dua pembacaan mentah.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

ENTRY_TOPUP = "topup"
ENTRY_CALIBRATION = "calibration"
ENTRY_RESET = "reset"

HOLD_ACTION_ACCEPT = "accept"
HOLD_ACTION_IGNORE = "ignore"
HOLD_ACTION_CALIBRATE = "calibrate"
HOLD_ACTIONS = (HOLD_ACTION_ACCEPT, HOLD_ACTION_IGNORE, HOLD_ACTION_CALIBRATE)

# Kalau pembacaan pertama SESUDAH reset counter melebihi ambang ini, ledger
# ditahan dan user diminta memutuskan. Lihat docs/decisions.md D-007.
DEFAULT_RESET_HOLD_THRESHOLD_KWH = 1.0


@dataclass
class TokenLedgerState:
    """State ledger token, wajib bertahan lintas restart."""

    baseline_group_total: float | None = None
    """Total energi kelompok saat penghitungan token dimulai."""

    credited_base_kwh: float = 0.0
    """kWh yang diakui saat kalibrasi terakhir, sebelum ditambah top-up baru."""

    entries: list[dict[str, Any]] = field(default_factory=list)
    """Seluruh riwayat: top-up, kalibrasi, dan reset. Tidak pernah dihapus
    otomatis - hanya user yang bisa menghapus lewat ``delete_topup``."""

    hold: dict[str, Any] | None = None
    """Kalau terisi, ledger sedang ditahan menunggu keputusan user."""

    seen_resets: dict[str, int] = field(default_factory=dict)
    """Jumlah reset yang sudah diketahui per sumber, supaya reset yang sama
    tidak terdeteksi berulang kali."""

    def as_dict(self) -> dict[str, Any]:
        """Bentuk yang disimpan ke .storage."""
        return {
            "baseline_group_total": self.baseline_group_total,
            "credited_base_kwh": self.credited_base_kwh,
            "entries": [dict(entry) for entry in self.entries],
            "hold": dict(self.hold) if self.hold else None,
            "seen_resets": dict(self.seen_resets),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TokenLedgerState:
        """Baca kembali dari .storage."""
        if not data:
            return cls()
        baseline = data.get("baseline_group_total")
        return cls(
            baseline_group_total=None if baseline is None else float(baseline),
            credited_base_kwh=float(data.get("credited_base_kwh", 0.0) or 0.0),
            entries=[dict(entry) for entry in (data.get("entries") or [])],
            hold=dict(data["hold"]) if data.get("hold") else None,
            seen_resets={
                str(key): int(value)
                for key, value in (data.get("seen_resets") or {}).items()
            },
        )


class TokenLedger:
    """Menghitung sisa token dari riwayat pengisian dan pemakaian."""

    def __init__(self, state: TokenLedgerState | None = None) -> None:
        """Siapkan ledger."""
        self.state = state if state is not None else TokenLedgerState()

    # ------------------------------------------------------------------
    # pembacaan
    # ------------------------------------------------------------------

    @property
    def started(self) -> bool:
        """True bila pencatatan token sudah dimulai."""
        return self.state.baseline_group_total is not None

    @property
    def on_hold(self) -> bool:
        """True bila ledger sedang ditahan menunggu keputusan user."""
        return self.state.hold is not None

    @property
    def active_topups(self) -> list[dict[str, Any]]:
        """Top-up yang masih dihitung (belum digantikan kalibrasi/reset)."""
        return [
            entry
            for entry in self.state.entries
            if entry.get("kind") == ENTRY_TOPUP and not entry.get("superseded")
        ]

    @property
    def total_credited_kwh(self) -> float:
        """Total kWh yang sudah diisi, dihitung ulang dari riwayat."""
        return self.state.credited_base_kwh + sum(
            float(entry.get("kwh_credited", 0.0) or 0.0)
            for entry in self.active_topups
        )

    def consumed_kwh(self, group_total: float | None) -> float | None:
        """Pemakaian sejak titik awal ledger.

        Saat ledger ditahan, angkanya dibekukan di posisi ketika penahanan
        dimulai - supaya lonjakan akibat reset counter tidak langsung memotong
        sisa token sebelum user sempat memutuskan.
        """
        baseline = self.state.baseline_group_total
        if baseline is None:
            return None
        if self.state.hold is not None:
            frozen = self.state.hold.get("group_total_at_hold")
            if frozen is not None:
                return max(0.0, float(frozen) - baseline)
        if group_total is None:
            return None
        return max(0.0, group_total - baseline)

    def remaining_kwh(self, group_total: float | None) -> float | None:
        """Sisa token dalam kWh."""
        consumed = self.consumed_kwh(group_total)
        if consumed is None:
            return None
        return self.total_credited_kwh - consumed

    # ------------------------------------------------------------------
    # perubahan
    # ------------------------------------------------------------------

    def add_topup(
        self,
        *,
        kwh_credited: float,
        group_total: float | None,
        timestamp: str,
        nominal_rp: float | None = None,
        meter_reading_before: float | None = None,
        meter_reading_after: float | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Catat pengisian token baru. Selalu menambah, tidak pernah mengganti."""
        if self.state.baseline_group_total is None:
            # Top-up pertama sekaligus menjadi titik awal penghitungan.
            self.state.baseline_group_total = group_total if group_total is not None else 0.0

        entry = {
            "id": uuid4().hex[:12],
            "kind": ENTRY_TOPUP,
            "timestamp": timestamp,
            "kwh_credited": float(kwh_credited),
            "nominal_rp": nominal_rp,
            "meter_reading_before": meter_reading_before,
            "meter_reading_after": meter_reading_after,
            "note": note,
        }
        self.state.entries.append(entry)
        return entry

    def edit_topup(self, topup_id: str, **changes: Any) -> dict[str, Any] | None:
        """Perbaiki satu entri top-up yang salah input."""
        for entry in self.state.entries:
            if entry.get("id") != topup_id or entry.get("kind") != ENTRY_TOPUP:
                continue
            for key, value in changes.items():
                if value is not None:
                    entry[key] = value
            return entry
        return None

    def delete_topup(self, topup_id: str) -> bool:
        """Hapus satu entri top-up yang tidak seharusnya ada."""
        for index, entry in enumerate(self.state.entries):
            if entry.get("id") == topup_id and entry.get("kind") == ENTRY_TOPUP:
                del self.state.entries[index]
                return True
        return False

    def calibrate(
        self,
        *,
        actual_remaining_kwh: float,
        group_total: float | None,
        timestamp: str,
        note: str | None = None,
    ) -> None:
        """Samakan ledger dengan angka yang tertera di layar meteran fisik.

        Riwayat lama tidak dihapus, hanya ditandai sudah digantikan - supaya
        tetap bisa ditelusuri, tapi tidak lagi ikut dihitung.
        """
        self._supersede_active_topups()
        self.state.baseline_group_total = (
            group_total if group_total is not None else 0.0
        )
        self.state.credited_base_kwh = float(actual_remaining_kwh)
        self.state.hold = None
        self.state.entries.append(
            {
                "id": uuid4().hex[:12],
                "kind": ENTRY_CALIBRATION,
                "timestamp": timestamp,
                "actual_remaining_kwh": float(actual_remaining_kwh),
                "group_total": group_total,
                "note": note,
            }
        )

    def reset(
        self, *, group_total: float | None, timestamp: str, note: str | None = None
    ) -> None:
        """Mulai ledger dari nol, misalnya setelah meteran fisik diganti."""
        self._supersede_active_topups()
        self.state.baseline_group_total = (
            group_total if group_total is not None else 0.0
        )
        self.state.credited_base_kwh = 0.0
        self.state.hold = None
        self.state.entries.append(
            {
                "id": uuid4().hex[:12],
                "kind": ENTRY_RESET,
                "timestamp": timestamp,
                "group_total": group_total,
                "note": note,
            }
        )

    def _supersede_active_topups(self) -> None:
        """Tandai top-up lama sebagai sudah digantikan, tanpa menghapusnya."""
        for entry in self.active_topups:
            entry["superseded"] = True

    # ------------------------------------------------------------------
    # penahanan ledger saat reset counter besar
    # ------------------------------------------------------------------

    def engage_hold(
        self,
        *,
        source_name: str,
        reset_from: float | None,
        reset_to: float | None,
        group_total: float | None,
        timestamp: str,
    ) -> None:
        """Bekukan ledger sampai user memutuskan apa arti reset ini."""
        if self.state.hold is not None:
            return
        self.state.hold = {
            "since": timestamp,
            "source_name": source_name,
            "reset_from": reset_from,
            "reset_to": reset_to,
            "group_total_at_hold": group_total,
        }

    def resolve_hold(
        self,
        *,
        action: str,
        group_total: float | None,
        timestamp: str,
        actual_remaining_kwh: float | None = None,
    ) -> bool:
        """Lepaskan penahanan sesuai keputusan user.

        * ``accept``    - anggap lonjakan itu pemakaian nyata; ledger lanjut apa
          adanya dan angkanya dipotong dari sisa token.
        * ``ignore``    - meteran diganti atau reset palsu; lonjakannya tidak
          dipotong dari sisa token.
        * ``calibrate`` - user memasukkan angka sisa yang tertera di meteran.
        """
        if self.state.hold is None:
            return False

        if action == HOLD_ACTION_CALIBRATE:
            if actual_remaining_kwh is None:
                return False
            self.calibrate(
                actual_remaining_kwh=actual_remaining_kwh,
                group_total=group_total,
                timestamp=timestamp,
                note="Kalibrasi setelah reset counter terdeteksi",
            )
            return True

        if action == HOLD_ACTION_IGNORE:
            # Geser titik awal sejauh lonjakan yang terjadi selama penahanan,
            # sehingga lonjakan itu tidak pernah terhitung sebagai pemakaian.
            frozen = self.state.hold.get("group_total_at_hold")
            if (
                frozen is not None
                and group_total is not None
                and self.state.baseline_group_total is not None
            ):
                self.state.baseline_group_total += group_total - float(frozen)

        self.state.hold = None
        return True
