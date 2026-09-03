"""Perhitungan biaya listrik dalam Rupiah.

*Pure Python*, supaya seluruh aritmetikanya bisa diuji tanpa Home Assistant.

Prinsip dari spec F.3 yang dipegang di sini:

* Biaya dihitung **saat pemakaian terjadi**, dengan tarif yang berlaku saat itu::

      cost_increment = energy_delta_kwh * tarif_aktif_saat_itu
      cost_total    += cost_increment

  Konsekuensinya, kalau tarif naik di tengah bulan, biaya yang sudah tercatat
  tetap memakai tarif lama. Itu benar secara akuntansi dan tidak perlu dihitung
  ulang surut (spec K.7).
* **Biaya beban tidak dicampur ke ``cost_total``**. Ia hanya ditambahkan di
  sensor rollup bulanan dan tahunan, supaya penghitung utama tetap murni berisi
  biaya dari energi yang benar-benar dipakai.
* **Pembulatan hanya di titik tampilan**, tidak pernah mengubah angka mentah -
  supaya galat pembulatan tidak menumpuk di penghitung jangka panjang.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from math import ceil, floor
from typing import Any

ROUNDING_NEAREST = "nearest"
ROUNDING_UP = "up"
ROUNDING_DOWN = "down"
ROUNDING_MODES = (ROUNDING_NEAREST, ROUNDING_UP, ROUNDING_DOWN)

FIXED_CHARGE_DAILY = "daily"
FIXED_CHARGE_MONTHLY = "monthly"
FIXED_CHARGE_PERIODS = (FIXED_CHARGE_DAILY, FIXED_CHARGE_MONTHLY)

# Rata-rata panjang bulan dalam setahun kalender Gregorian. Dipakai untuk
# menyebar biaya beban bulanan jadi biaya harian, supaya penghitung bulanan dan
# tahunan naik mulus, bukan melompat di tanggal 1.
DAYS_PER_MONTH = 365.25 / 12


@dataclass(frozen=True)
class TariffConfig:
    """Tarif yang berlaku untuk satu kelompok tagihan."""

    rate_rp_per_kwh: float
    fixed_charge_rp: float = 0.0
    fixed_charge_period: str = FIXED_CHARGE_MONTHLY
    rounding_mode: str = ROUNDING_NEAREST
    rounding_unit_rp: float = 1.0

    @property
    def fixed_charge_per_day(self) -> float:
        """Biaya beban dinyatakan sebagai Rupiah per hari."""
        if self.fixed_charge_rp <= 0:
            return 0.0
        if self.fixed_charge_period == FIXED_CHARGE_DAILY:
            return self.fixed_charge_rp
        return self.fixed_charge_rp / DAYS_PER_MONTH

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TariffConfig:
        """Baca tarif dari data subentry, toleran terhadap isian yang aneh."""
        data = data or {}
        try:
            rate = float(data.get("rate_rp_per_kwh", 0.0) or 0.0)
        except (TypeError, ValueError):
            rate = 0.0
        try:
            fixed = float(data.get("fixed_charge_rp", 0.0) or 0.0)
        except (TypeError, ValueError):
            fixed = 0.0
        try:
            unit = float(data.get("rounding_unit_rp", 1.0) or 1.0)
        except (TypeError, ValueError):
            unit = 1.0

        period = str(data.get("fixed_charge_period", FIXED_CHARGE_MONTHLY))
        if period not in FIXED_CHARGE_PERIODS:
            period = FIXED_CHARGE_MONTHLY
        mode = str(data.get("rounding_mode", ROUNDING_NEAREST))
        if mode not in ROUNDING_MODES:
            mode = ROUNDING_NEAREST

        return cls(
            rate_rp_per_kwh=max(0.0, rate),
            fixed_charge_rp=max(0.0, fixed),
            fixed_charge_period=period,
            rounding_mode=mode,
            rounding_unit_rp=unit,
        )


def apply_rounding(value: float | None, tariff: TariffConfig) -> float | None:
    """Bulatkan angka Rupiah untuk ditampilkan.

    Tidak pernah dipakai untuk mengubah angka yang disimpan - lihat catatan di
    docstring modul.
    """
    if value is None:
        return None
    unit = tariff.rounding_unit_rp
    if unit <= 0:
        return value
    quotient = value / unit
    if tariff.rounding_mode == ROUNDING_UP:
        return ceil(quotient) * unit
    if tariff.rounding_mode == ROUNDING_DOWN:
        return floor(quotient) * unit
    return round(quotient) * unit


@dataclass
class CostTotalState:
    """State penghitung biaya, wajib bertahan lintas restart."""

    total_rp: float = 0.0
    energy_prev: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Bentuk yang disimpan ke .storage."""
        return {"total_rp": self.total_rp, "energy_prev": self.energy_prev}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> CostTotalState:
        """Baca kembali dari .storage."""
        if not data:
            return cls()
        energy_prev = data.get("energy_prev")
        return cls(
            total_rp=float(data.get("total_rp", 0.0) or 0.0),
            energy_prev=None if energy_prev is None else float(energy_prev),
        )


class CostAccumulator:
    """Mengubah kenaikan kWh menjadi Rupiah, memakai tarif yang berlaku saat itu."""

    def __init__(self, state: CostTotalState | None = None) -> None:
        """Siapkan penghitung biaya."""
        self.state = state if state is not None else CostTotalState()

    def update(self, energy_total: float | None, rate_rp_per_kwh: float) -> float | None:
        """Catat pemakaian baru dengan tarif yang berlaku sekarang."""
        if energy_total is None:
            return None if self.state.energy_prev is None else self.state.total_rp

        state = self.state
        if state.energy_prev is None:
            # Titik awal: belum ada pemakaian yang bisa ditagihkan.
            state.energy_prev = energy_total
            return state.total_rp

        delta = energy_total - state.energy_prev
        if delta > 0:
            state.total_rp += delta * rate_rp_per_kwh
        state.energy_prev = energy_total
        return state.total_rp


def fixed_charge_accrued(
    tariff: TariffConfig, cycle_start: datetime | None, now: datetime
) -> float:
    """Biaya beban yang sudah berjalan sejak awal siklus.

    Disebar merata per hari, bukan ditagihkan sekaligus di awal siklus, supaya
    angka di dashboard naik mulus dan tidak melompat di tanggal 1.
    """
    per_day = tariff.fixed_charge_per_day
    if per_day <= 0 or cycle_start is None:
        return 0.0
    elapsed_days = (now - cycle_start).total_seconds() / 86400
    if elapsed_days <= 0:
        return 0.0
    return per_day * elapsed_days


@dataclass
class RateHistoryEntry:
    """Satu versi tarif, disimpan untuk audit."""

    effective_from: str
    rate_rp_per_kwh: float

    def as_dict(self) -> dict[str, Any]:
        """Bentuk yang disimpan di subentry."""
        return {
            "effective_from": self.effective_from,
            "rate_rp_per_kwh": self.rate_rp_per_kwh,
        }


def append_rate_version(
    history: list[dict[str, Any]] | None,
    rate_rp_per_kwh: float,
    effective_from: str,
) -> list[dict[str, Any]]:
    """Tambahkan versi tarif baru, jangan pernah menimpa yang lama (spec K.7).

    Riwayat ini murni untuk audit: ``cost_total`` tidak pernah dihitung ulang
    surut, karena tiap kenaikan biaya sudah memakai tarif yang berlaku saat
    pemakaian itu terjadi.
    """
    versions = list(history or [])
    if versions and float(versions[-1].get("rate_rp_per_kwh", 0)) == rate_rp_per_kwh:
        return versions
    versions.append(
        RateHistoryEntry(
            effective_from=effective_from, rate_rp_per_kwh=rate_rp_per_kwh
        ).as_dict()
    )
    return versions
