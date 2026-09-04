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


# --- nilai pengisian siap pakai ----------------------------------------------

# Batas kewajaran satu kali pengisian token. Ini bukan kebijakan, melainkan
# pagar salah ketik: pembelian Rp 1 juta pada golongan rumah tangga menghasilkan
# ratusan kWh, jadi angka di atas ini hampir pasti salah satuan.
#
# Kasus yang benar-benar terjadi: struk PLN menulis jumlah kWh dalam satuan
# 0,01 kWh - "82650 KWM" di struk berarti 826,50 kWh di layar meteran. User yang
# menyalin 82650 apa adanya akan tertahan di sini, lengkap dengan saran
# pembagian 100.
MAX_PLAUSIBLE_TOPUP_KWH = 20000.0
KWM_PER_KWH = 100


@dataclass(frozen=True)
class TokenPreset:
    """Satu nilai pengisian yang sering dipakai user."""

    nominal_rp: float
    kwh: float

    def as_dict(self) -> dict[str, Any]:
        """Bentuk yang disimpan di subentry."""
        return {"nominal_rp": self.nominal_rp, "kwh": self.kwh}

    @property
    def label(self) -> str:
        """Label ringkas untuk tombol dashboard."""
        nominal = f"{self.nominal_rp:,.0f}".replace(",", ".")
        kwh = f"{self.kwh:,.2f}".replace(",", "\x00").replace(".", ",")
        kwh = kwh.replace("\x00", ".")
        return f"Rp {nominal} ({kwh} kWh)"


def parse_rupiah(text: str) -> float | None:
    """Baca nominal rupiah. Selalu bilangan bulat, jadi cukup ambil angkanya."""
    digits = "".join(char for char in str(text) if char.isdigit())
    if not digits:
        return None
    return float(digits)


def parse_kwh(text: str) -> float | None:
    """Baca angka kWh, menerima gaya Indonesia maupun Inggris.

    ``826,50`` dan ``826.50`` sama-sama diterima. Kalau ada koma, titik
    dianggap pemisah ribuan - jadi ``1.234,56`` juga terbaca benar.
    """
    cleaned = str(text).strip().replace(" ", "")
    if not cleaned:
        return None
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_presets(text: str | None) -> tuple[list[TokenPreset], list[str]]:
    """Baca daftar preset dari teks, satu baris satu nilai.

    Bentuk tiap baris: ``nominal = kwh``, misalnya ``1.000.000 = 826,50``.
    Mengembalikan pasangan (preset yang berhasil dibaca, baris yang gagal).
    """
    presets: list[TokenPreset] = []
    bad_lines: list[str] = []

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        left, separator, right = line.partition("=")
        if not separator:
            bad_lines.append(line)
            continue

        nominal = parse_rupiah(left)
        kwh = parse_kwh(right)
        if nominal is None or kwh is None or nominal <= 0 or kwh <= 0:
            bad_lines.append(line)
            continue

        presets.append(TokenPreset(nominal_rp=nominal, kwh=kwh))

    return presets, bad_lines


def format_presets(presets: list[dict[str, Any]] | None) -> str:
    """Tulis kembali daftar preset jadi teks untuk ditampilkan di form."""
    lines = []
    for preset in presets or []:
        nominal = f"{float(preset['nominal_rp']):,.0f}".replace(",", ".")
        kwh = f"{float(preset['kwh']):.2f}".replace(".", ",")
        lines.append(f"{nominal} = {kwh}")
    return "\n".join(lines)


def load_presets(data: list[dict[str, Any]] | None) -> list[TokenPreset]:
    """Baca preset dari data subentry."""
    presets: list[TokenPreset] = []
    for entry in data or []:
        try:
            presets.append(
                TokenPreset(
                    nominal_rp=float(entry["nominal_rp"]), kwh=float(entry["kwh"])
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return presets


def find_preset(
    presets: list[TokenPreset], nominal_rp: float | None
) -> TokenPreset | None:
    """Cari preset yang cocok dengan nominal pembelian."""
    if nominal_rp is None:
        return None
    for preset in presets:
        if abs(preset.nominal_rp - nominal_rp) < 0.01:
            return preset
    return None


def implausible_kwh_hint(kwh: float) -> float | None:
    """Kalau angkanya tidak masuk akal, tebak maksud user (satuan KWM struk)."""
    if kwh <= MAX_PLAUSIBLE_TOPUP_KWH:
        return None
    return kwh / KWM_PER_KWH
