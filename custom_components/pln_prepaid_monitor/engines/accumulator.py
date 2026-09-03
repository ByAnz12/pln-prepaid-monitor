"""Akumulator kWh yang aman terhadap reset counter fisik.

Modul ini sengaja *pure Python* (tidak mengimpor Home Assistant) supaya
logikanya bisa diuji sebagai fungsi biasa, terpisah dari runtime HA.

Algoritmanya adalah cerminan persis dari cara Home Assistant Core sendiri
menghitung kolom ``sum`` long-term statistics untuk sensor
``state_class: total_increasing``. Diverifikasi langsung terhadap source
Core 2026.8.3:

* ``homeassistant/components/sensor/recorder.py::reset_detected`` (baris 475)
  - dip terjadi bila ``0.9 * previous <= new < previous`` -> hanya dicatat
    sebagai peringatan, BUKAN reset;
  - nilai negatif -> reading dibuang total;
  - reset genuine bila ``new < 0.9 * previous``.
* ``homeassistant/components/sensor/recorder.py::compile_statistics``
  (baris 798-818) - saat reset: siklus lama ditutup dengan
  ``sum += raw_prev - zero_point``, lalu titik nol siklus berikutnya di-set
  ke ``0`` (bukan ke nilai baru), sehingga seluruh pembacaan pasca-reset
  terhitung penuh sebagai konsumsi.

Total konsumsi pada saat mana pun karena itu adalah::

    consumed = banked + (raw_prev - zero_point)

Nilai yang dipublikasikan ke entity adalah ``offset + consumed``, di mana
``offset`` di-seed dengan pembacaan mentah pertama supaya angkanya bisa
dicocokkan langsung dengan "total forward energy" di aplikasi meter
(mis. Smart Life) - lihat README bagian troubleshooting.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Any

# Ambang deteksi reset milik HA Core: turun lebih dari 10% = reset genuine.
RESET_DIP_RATIO = 0.9


class AccumulatorEvent(StrEnum):
    """Apa yang terjadi pada satu pembacaan."""

    INITIALIZED = "initialized"
    """Pembacaan pertama: jadi titik nol, tidak menambah konsumsi apa pun."""

    NORMAL = "normal"
    """Naik seperti biasa (atau tetap): selisihnya dihitung sebagai konsumsi."""

    DIP = "dip"
    """Turun <=10%: dianggap noise/pembulatan, diikuti apa adanya."""

    RESET = "reset"
    """Turun >10%: counter fisik dianggap reset, siklus lama ditutup."""

    NEGATIVE_IGNORED = "negative_ignored"
    """Nilai negatif: dibuang, tidak pernah diproses (seperti HA Core)."""

    INVALID_IGNORED = "invalid_ignored"
    """Bukan angka / tak hingga: dibuang."""


@dataclass
class AccumulatorState:
    """State akumulator yang wajib bertahan lintas restart Home Assistant."""

    raw_prev: float | None = None
    """Pembacaan mentah terakhir dari sensor sumber (sudah dinormalisasi ke kWh)."""

    zero_point: float = 0.0
    """Titik nol siklus yang sedang berjalan."""

    banked: float = 0.0
    """Konsumsi dari siklus-siklus yang sudah ditutup (sebelum reset)."""

    offset: float = 0.0
    """Nilai awal yang ditambahkan ke hasil, supaya angka mirip meter fisik."""

    resets_detected: int = 0
    dips_detected: int = 0
    negatives_ignored: int = 0

    @property
    def consumed(self) -> float:
        """Total kWh yang dikonsumsi sejak akumulator dimulai."""
        if self.raw_prev is None:
            return 0.0
        return self.banked + (self.raw_prev - self.zero_point)

    @property
    def total(self) -> float | None:
        """Nilai yang dipublikasikan ke entity, atau None bila belum ada data."""
        if self.raw_prev is None:
            return None
        return self.offset + self.consumed

    def as_dict(self) -> dict[str, Any]:
        """Bentuk yang disimpan ke .storage."""
        return {
            "raw_prev": self.raw_prev,
            "zero_point": self.zero_point,
            "banked": self.banked,
            "offset": self.offset,
            "resets_detected": self.resets_detected,
            "dips_detected": self.dips_detected,
            "negatives_ignored": self.negatives_ignored,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> AccumulatorState:
        """Baca kembali dari .storage, toleran terhadap data tidak lengkap."""
        if not data:
            return cls()
        raw_prev = data.get("raw_prev")
        return cls(
            raw_prev=None if raw_prev is None else float(raw_prev),
            zero_point=float(data.get("zero_point", 0.0)),
            banked=float(data.get("banked", 0.0)),
            offset=float(data.get("offset", 0.0)),
            resets_detected=int(data.get("resets_detected", 0)),
            dips_detected=int(data.get("dips_detected", 0)),
            negatives_ignored=int(data.get("negatives_ignored", 0)),
        )


class ResetSafeAccumulator:
    """Menerjemahkan deret pembacaan mentah jadi total kWh yang aman-reset."""

    def __init__(
        self,
        state: AccumulatorState | None = None,
        *,
        seed_offset: bool = True,
    ) -> None:
        """Siapkan akumulator.

        :param seed_offset: bila True, pembacaan pertama dipakai sebagai offset
            sehingga nilai entity mengikuti angka meter fisik. Bila False,
            akumulator mulai dari 0.
        """
        self.state = state if state is not None else AccumulatorState()
        self._seed_offset = seed_offset

    def update(self, raw: Any) -> AccumulatorEvent:
        """Proses satu pembacaan mentah, kembalikan apa yang terjadi."""
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return AccumulatorEvent.INVALID_IGNORED
        if not isfinite(value):
            return AccumulatorEvent.INVALID_IGNORED

        state = self.state

        # Sama seperti HA Core: nilai negatif tidak pernah diproses, baik
        # sebagai konsumsi maupun sebagai reset.
        if value < 0:
            state.negatives_ignored += 1
            return AccumulatorEvent.NEGATIVE_IGNORED

        if state.raw_prev is None:
            state.raw_prev = value
            state.zero_point = value
            if self._seed_offset:
                state.offset = value
            return AccumulatorEvent.INITIALIZED

        if value < RESET_DIP_RATIO * state.raw_prev:
            # Reset genuine: tutup siklus lama, mulai siklus baru dari nol.
            state.banked += state.raw_prev - state.zero_point
            state.zero_point = 0.0
            state.raw_prev = value
            state.resets_detected += 1
            return AccumulatorEvent.RESET

        if value < state.raw_prev:
            # Turun <=10%: HA Core memperlakukan ini sebagai noise dan tetap
            # memakai nilainya apa adanya. Kita ikuti persis supaya angka kita
            # tidak pernah berbeda dari statistics bawaan HA.
            state.dips_detected += 1
            state.raw_prev = value
            return AccumulatorEvent.DIP

        state.raw_prev = value
        return AccumulatorEvent.NORMAL


@dataclass
class IntegratorState:
    """State integrasi daya (W) menjadi energi (kWh) - Riemann sum kiri."""

    total_kwh: float = 0.0
    last_power_w: float | None = None
    last_timestamp: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Bentuk yang disimpan ke .storage."""
        return {
            "total_kwh": self.total_kwh,
            "last_power_w": self.last_power_w,
            "last_timestamp": self.last_timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> IntegratorState:
        """Baca kembali dari .storage."""
        if not data:
            return cls()
        last_power = data.get("last_power_w")
        last_ts = data.get("last_timestamp")
        return cls(
            total_kwh=float(data.get("total_kwh", 0.0)),
            last_power_w=None if last_power is None else float(last_power),
            last_timestamp=None if last_ts is None else float(last_ts),
        )


class PowerIntegrator:
    """Fallback bila sumber hanya punya sensor daya, tanpa sensor kWh.

    Memakai Riemann sum kiri persis seperti rumus di spec F.1::

        kWh += (power_W * delta_t_jam) / 1000

    Hasilnya *estimasi*, dan entity yang memakainya ditandai
    ``source_of_truth: integrated_from_power`` supaya user tahu angka ini
    tidak sekelas pembacaan kWh kumulatif asli.
    """

    def __init__(self, state: IntegratorState | None = None) -> None:
        """Siapkan integrator."""
        self.state = state if state is not None else IntegratorState()

    def add_sample(self, power_w: float | None, timestamp: float) -> float:
        """Tambahkan satu sampel daya, kembalikan total kWh terkini."""
        state = self.state
        if (
            state.last_power_w is not None
            and state.last_timestamp is not None
            and timestamp > state.last_timestamp
        ):
            elapsed_hours = (timestamp - state.last_timestamp) / 3600
            state.total_kwh += (state.last_power_w * elapsed_hours) / 1000

        state.last_power_w = power_w
        state.last_timestamp = timestamp
        return state.total_kwh

    def pause(self) -> None:
        """Hentikan integrasi karena sumber hilang.

        Jeda tidak pernah diisi mundur: saat sumber kembali, integrasi dimulai
        lagi dari titik itu. Ini keputusan sadar sesuai spec K.2 - gap tetap
        tercatat sebagai gap, bukan ditebak diam-diam.
        """
        self.state.last_power_w = None
        self.state.last_timestamp = None
