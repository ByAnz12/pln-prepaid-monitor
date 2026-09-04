"""Perkiraan kapan token habis.

*Pure Python*, supaya seluruh aturan dan pengamannya bisa diuji tanpa Home
Assistant dan tanpa database.

Prinsip yang dipegang (spec H):

* **Jangan pernah menampilkan angka presisi kalau datanya belum cukup.**
  Lebih baik jujur "belum bisa diperkirakan" daripada memberi tanggal yang
  terdengar pasti padahal ditebak dari dua hari data.
* Setiap hasil membawa **tingkat keyakinan** dan **rentang data mana yang
  dipakai**, supaya user bisa menilai sendiri seberapa serius angkanya.
* Ada **margin aman**: perkiraan sengaja dibuat sedikit pesimistis, karena
  kehabisan token lebih merepotkan daripada mengisi sedikit lebih awal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import fmean, median
from typing import Any

WINDOW_24H = "24h"
WINDOW_7D = "7d"
WINDOW_30D = "30d"
WINDOWS = (WINDOW_24H, WINDOW_7D, WINDOW_30D)

# Urutan cadangan sesuai spec D.1: coba pilihan user dulu, lalu sisanya.
DEFAULT_FALLBACK_ORDER = (WINDOW_7D, WINDOW_24H, WINDOW_30D)

OUTLIER_NONE = "none"
OUTLIER_MEDIAN = "median"
OUTLIER_TRIM = "trim_percent"
OUTLIER_FILTERS = (OUTLIER_NONE, OUTLIER_MEDIAN, OUTLIER_TRIM)

CONFIDENCE_INSUFFICIENT_DATA = "insufficient_data"
CONFIDENCE_INSUFFICIENT_USAGE = "insufficient_usage"
CONFIDENCE_LOW = "low"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_HIGH = "high"

STATUS_NORMAL = "normal"
STATUS_WARNING = "warning"
STATUS_CRITICAL = "critical"
STATUS_VERY_CRITICAL = "very_critical"
STATUS_UNKNOWN = "unknown"
STATUS_HOLD = "hold"
STATUSES = (
    STATUS_NORMAL,
    STATUS_WARNING,
    STATUS_CRITICAL,
    STATUS_VERY_CRITICAL,
    STATUS_HOLD,
    STATUS_UNKNOWN,
)

# Di bawah ini pemakaian dianggap nol; membagi dengannya hanya menghasilkan
# angka tak berhingga yang menyesatkan.
USAGE_EPSILON_KWH = 1e-6


@dataclass(frozen=True)
class WindowSpec:
    """Bagaimana satu rentang waktu dibaca dari statistik."""

    key: str
    period: str
    span: timedelta
    per_day_factor: float


WINDOW_SPECS: dict[str, WindowSpec] = {
    WINDOW_24H: WindowSpec(WINDOW_24H, "hour", timedelta(hours=24), 24.0),
    WINDOW_7D: WindowSpec(WINDOW_7D, "day", timedelta(days=7), 1.0),
    WINDOW_30D: WindowSpec(WINDOW_30D, "day", timedelta(days=30), 1.0),
}


@dataclass(frozen=True)
class PredictionConfig:
    """Pengaturan cara memperkirakan."""

    preferred_window: str = WINDOW_7D
    min_data_points: int = 3
    min_data_points_hours: int = 6
    outlier_filter: str = OUTLIER_MEDIAN
    safety_margin_percent: float = 10.0
    trim_percent: float = 10.0

    def minimum_points(self, window: str) -> int:
        """Berapa titik data minimum sebelum sebuah rentang dianggap cukup."""
        if window == WINDOW_24H:
            return self.min_data_points_hours
        return self.min_data_points

    @property
    def fallback_order(self) -> tuple[str, ...]:
        """Rentang yang dicoba, mulai dari pilihan user."""
        rest = tuple(w for w in DEFAULT_FALLBACK_ORDER if w != self.preferred_window)
        return (self.preferred_window, *rest)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> PredictionConfig:
        """Baca dari konfigurasi subentry, toleran terhadap isian yang aneh."""
        data = data or {}

        window = str(data.get("preferred_window", WINDOW_7D))
        if window not in WINDOWS:
            window = WINDOW_7D

        outlier = str(data.get("outlier_filter", OUTLIER_MEDIAN))
        if outlier not in OUTLIER_FILTERS:
            outlier = OUTLIER_MEDIAN

        def _number(key: str, fallback: float) -> float:
            try:
                return float(data.get(key, fallback))
            except (TypeError, ValueError):
                return fallback

        return cls(
            preferred_window=window,
            min_data_points=max(1, int(_number("min_data_points", 3))),
            outlier_filter=outlier,
            safety_margin_percent=max(0.0, _number("safety_margin_percent", 10.0)),
        )


@dataclass(frozen=True)
class TokenThresholds:
    """Kapan status token naik tingkat."""

    warning_days: float = 7.0
    critical_days: float = 3.0
    very_critical_days: float = 1.0
    low_kwh: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TokenThresholds:
        """Baca ambang dari konfigurasi subentry."""
        data = data or {}

        def _number(key: str, fallback: float) -> float:
            try:
                return float(data.get(key, fallback))
            except (TypeError, ValueError):
                return fallback

        low_kwh = data.get("token_low_kwh_threshold")
        try:
            low_value = float(low_kwh) if low_kwh is not None else 0.0
        except (TypeError, ValueError):
            low_value = 0.0

        return cls(
            warning_days=_number("warning_threshold_days", 7.0),
            critical_days=_number("critical_threshold_days", 3.0),
            very_critical_days=_number("very_critical_threshold_days", 1.0),
            low_kwh=low_value if low_value > 0 else None,
        )


@dataclass
class PredictionResult:
    """Hasil satu kali perhitungan perkiraan."""

    avg_daily_kwh: float | None = None
    window_used: str | None = None
    data_points: int = 0
    confidence: str = CONFIDENCE_INSUFFICIENT_DATA
    days_remaining: float | None = None
    empty_date: datetime | None = None

    @property
    def data_sufficient(self) -> bool:
        """True bila datanya sudah cukup untuk memberi angka."""
        return self.avg_daily_kwh is not None


def filtered_average(samples: list[float], config: PredictionConfig) -> float | None:
    """Rata-rata sampel, dengan peredam anomali sesuai pilihan user.

    Median dipakai sebagai bawaan karena satu hari dengan lonjakan luar biasa
    (tamu menginap, AC menyala seharian) tidak seharusnya menggeser perkiraan
    sebulan ke depan secara tidak proporsional.
    """
    values = [value for value in samples if value is not None and value >= 0]
    if not values:
        return None

    if config.outlier_filter == OUTLIER_MEDIAN:
        return float(median(values))

    if config.outlier_filter == OUTLIER_TRIM and len(values) >= 3:
        ordered = sorted(values)
        drop = int(len(ordered) * config.trim_percent / 100)
        trimmed = ordered[drop : len(ordered) - drop] or ordered
        return float(fmean(trimmed))

    return float(fmean(values))


def choose_window(
    samples_by_window: dict[str, list[float]], config: PredictionConfig
) -> tuple[str, list[float]] | None:
    """Pilih rentang pertama yang datanya sudah cukup."""
    for window in config.fallback_order:
        samples = samples_by_window.get(window) or []
        if len(samples) >= config.minimum_points(window):
            return window, samples
    return None


def _confidence(window: str, data_points: int) -> str:
    """Seberapa jauh angka ini boleh dipercaya (spec H)."""
    if window == WINDOW_24H:
        return CONFIDENCE_MEDIUM
    if window == WINDOW_7D:
        return CONFIDENCE_HIGH if data_points >= 7 else CONFIDENCE_MEDIUM
    return CONFIDENCE_HIGH if data_points >= 30 else CONFIDENCE_LOW


def predict(
    *,
    remaining_kwh: float | None,
    samples_by_window: dict[str, list[float]],
    config: PredictionConfig,
    now: datetime,
) -> PredictionResult:
    """Hitung perkiraan hari tersisa dan tanggal habis."""
    chosen = choose_window(samples_by_window, config)
    if chosen is None:
        return PredictionResult(confidence=CONFIDENCE_INSUFFICIENT_DATA)

    window, samples = chosen
    spec = WINDOW_SPECS[window]
    average = filtered_average(samples, config)
    if average is None:
        return PredictionResult(
            window_used=window,
            data_points=len(samples),
            confidence=CONFIDENCE_INSUFFICIENT_DATA,
        )

    avg_daily = average * spec.per_day_factor
    result = PredictionResult(
        avg_daily_kwh=avg_daily,
        window_used=window,
        data_points=len(samples),
        confidence=_confidence(window, len(samples)),
    )

    if avg_daily <= USAGE_EPSILON_KWH:
        # Tanpa pemakaian, "berapa hari lagi habis" tidak punya jawaban -
        # dan membaginya hanya menghasilkan angka tak berhingga.
        result.confidence = CONFIDENCE_INSUFFICIENT_USAGE
        return result

    if remaining_kwh is None:
        return result

    adjusted = avg_daily * (1 + config.safety_margin_percent / 100)
    days = remaining_kwh / adjusted
    result.days_remaining = max(0.0, days)
    result.empty_date = now + timedelta(days=result.days_remaining)
    return result


def determine_status(
    *,
    days_remaining: float | None,
    remaining_kwh: float | None,
    thresholds: TokenThresholds,
    on_hold: bool = False,
) -> str:
    """Tentukan tingkat kegentingan token (spec I.1)."""
    if on_hold:
        # Selama ledger dibekukan, angkanya belum tentu mencerminkan keadaan
        # sebenarnya - jangan memberi rasa aman maupun panik yang palsu.
        return STATUS_HOLD

    if (
        thresholds.low_kwh is not None
        and remaining_kwh is not None
        and remaining_kwh <= thresholds.low_kwh
    ):
        return STATUS_VERY_CRITICAL

    if days_remaining is None:
        return STATUS_UNKNOWN
    if days_remaining <= thresholds.very_critical_days:
        return STATUS_VERY_CRITICAL
    if days_remaining <= thresholds.critical_days:
        return STATUS_CRITICAL
    if days_remaining <= thresholds.warning_days:
        return STATUS_WARNING
    return STATUS_NORMAL
