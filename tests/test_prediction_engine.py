"""Test aturan prediksi dan seluruh pengamannya - tanpa Home Assistant."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.pln_prepaid_monitor.engines.prediction_engine import (
    CONFIDENCE_HIGH,
    CONFIDENCE_INSUFFICIENT_DATA,
    CONFIDENCE_INSUFFICIENT_USAGE,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    OUTLIER_MEDIAN,
    OUTLIER_NONE,
    OUTLIER_TRIM,
    STATUS_CRITICAL,
    STATUS_HOLD,
    STATUS_NORMAL,
    STATUS_UNKNOWN,
    STATUS_VERY_CRITICAL,
    STATUS_WARNING,
    WINDOW_24H,
    WINDOW_7D,
    WINDOW_30D,
    PredictionConfig,
    TokenThresholds,
    determine_status,
    filtered_average,
    predict,
)

JAKARTA = ZoneInfo("Asia/Jakarta")
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=JAKARTA)


def _predict(remaining, samples, **config_kwargs):
    """Panggil predict dengan pengaturan yang ringkas ditulis."""
    return predict(
        remaining_kwh=remaining,
        samples_by_window=samples,
        config=PredictionConfig(**config_kwargs),
        now=NOW,
    )


# --- rata-rata dan peredam anomali -------------------------------------------


def test_median_filter_absorbs_one_extreme_day() -> None:
    """Satu hari dengan lonjakan luar biasa tidak menggeser perkiraan."""
    samples = [10.0, 10.0, 11.0, 9.0, 10.0, 10.0, 90.0]
    config = PredictionConfig(outlier_filter=OUTLIER_MEDIAN)

    assert filtered_average(samples, config) == pytest.approx(10.0)
    # Tanpa peredam, satu hari itu menyeret rata-rata jauh ke atas.
    assert filtered_average(samples, PredictionConfig(outlier_filter=OUTLIER_NONE)) > 20


def test_trim_filter_drops_both_ends() -> None:
    """Trim membuang sebagian ujung atas dan bawah lalu merata-ratakan."""
    samples = [1.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 100.0]
    config = PredictionConfig(outlier_filter=OUTLIER_TRIM, trim_percent=10)
    assert filtered_average(samples, config) == pytest.approx(10.0)


def test_average_ignores_negative_and_empty() -> None:
    """Sampel negatif dibuang; tanpa sampel sama sekali hasilnya None."""
    config = PredictionConfig(outlier_filter=OUTLIER_NONE)
    assert filtered_average([-5.0, 10.0, 10.0], config) == pytest.approx(10.0)
    assert filtered_average([], config) is None


# --- pemilihan rentang -------------------------------------------------------


def test_uses_preferred_window_when_data_is_enough() -> None:
    """Rentang pilihan user dipakai lebih dulu."""
    result = _predict(70.0, {WINDOW_7D: [10.0] * 7, WINDOW_24H: [1.0] * 24})
    assert result.window_used == WINDOW_7D
    assert result.confidence == CONFIDENCE_HIGH


def test_falls_back_when_preferred_window_is_too_new() -> None:
    """Baru dipasang dua hari: turun ke rentang 24 jam, keyakinan diturunkan."""
    result = _predict(70.0, {WINDOW_7D: [10.0, 10.0], WINDOW_24H: [0.5] * 24})

    assert result.window_used == WINDOW_24H
    assert result.confidence == CONFIDENCE_MEDIUM
    # 0,5 kWh per jam = 12 kWh per hari.
    assert result.avg_daily_kwh == pytest.approx(12.0)


def test_thirty_day_window_with_partial_data_is_low_confidence() -> None:
    """Rentang 30 hari yang belum penuh ditandai keyakinan rendah (spec H)."""
    result = _predict(
        100.0, {WINDOW_30D: [10.0] * 10}, preferred_window=WINDOW_30D
    )
    assert result.window_used == WINDOW_30D
    assert result.confidence == CONFIDENCE_LOW


def test_seven_day_window_with_few_points_is_medium() -> None:
    """Data 3-6 hari sudah boleh dipakai, tapi keyakinannya sedang."""
    result = _predict(70.0, {WINDOW_7D: [10.0] * 4})
    assert result.window_used == WINDOW_7D
    assert result.confidence == CONFIDENCE_MEDIUM


def test_minimum_data_points_is_configurable() -> None:
    """Ambang minimum data bisa dinaikkan user."""
    samples = {WINDOW_7D: [10.0] * 4, WINDOW_24H: [], WINDOW_30D: []}
    assert _predict(70.0, samples, min_data_points=4).window_used == WINDOW_7D
    assert _predict(70.0, samples, min_data_points=5).window_used is None


# --- pengaman ----------------------------------------------------------------


def test_insufficient_data_returns_unknown_not_a_guess() -> None:
    """Data belum cukup: tidak ada angka sama sekali, bukan tebakan."""
    result = _predict(70.0, {WINDOW_7D: [10.0], WINDOW_24H: [1.0, 1.0]})

    assert result.confidence == CONFIDENCE_INSUFFICIENT_DATA
    assert result.avg_daily_kwh is None
    assert result.days_remaining is None
    assert result.empty_date is None
    assert result.data_sufficient is False


def test_zero_usage_does_not_divide_by_zero() -> None:
    """Pemakaian nol menghasilkan 'belum bisa diperkirakan', bukan tak berhingga."""
    result = _predict(70.0, {WINDOW_7D: [0.0] * 7})

    assert result.confidence == CONFIDENCE_INSUFFICIENT_USAGE
    assert result.avg_daily_kwh == pytest.approx(0.0)
    assert result.days_remaining is None
    assert result.empty_date is None


def test_no_token_data_still_reports_average_usage() -> None:
    """Tanpa data token, rata-rata pemakaian tetap dilaporkan."""
    result = _predict(None, {WINDOW_7D: [10.0] * 7})

    assert result.avg_daily_kwh == pytest.approx(10.0)
    assert result.days_remaining is None


def test_days_remaining_is_never_negative() -> None:
    """Token yang sudah minus tidak menghasilkan hari negatif."""
    result = _predict(-5.0, {WINDOW_7D: [10.0] * 7})
    assert result.days_remaining == pytest.approx(0.0)


# --- margin aman -------------------------------------------------------------


def test_safety_margin_makes_the_estimate_pessimistic() -> None:
    """Perkiraan sengaja sedikit lebih cepat habis daripada hitungan lurus."""
    plain = _predict(100.0, {WINDOW_7D: [10.0] * 7}, safety_margin_percent=0)
    padded = _predict(100.0, {WINDOW_7D: [10.0] * 7}, safety_margin_percent=10)

    assert plain.days_remaining == pytest.approx(10.0)
    # 10 kWh/hari + margin 10% = 11 kWh/hari -> 100/11 hari.
    assert padded.days_remaining == pytest.approx(100 / 11)
    assert padded.days_remaining < plain.days_remaining


def test_empty_date_follows_days_remaining() -> None:
    """Tanggal habis dihitung dari sekarang plus hari tersisa."""
    result = _predict(110.0, {WINDOW_7D: [10.0] * 7}, safety_margin_percent=10)
    assert result.days_remaining == pytest.approx(10.0)
    assert result.empty_date == NOW + timedelta(days=10)


# --- konfigurasi -------------------------------------------------------------


def test_config_from_dict_tolerates_bad_input() -> None:
    """Isian rusak jatuh ke nilai bawaan, bukan membuat integrasi gagal."""
    config = PredictionConfig.from_dict(
        {
            "preferred_window": "sebulan",
            "outlier_filter": "acak",
            "min_data_points": "x",
            "safety_margin_percent": None,
        }
    )
    assert config.preferred_window == WINDOW_7D
    assert config.outlier_filter == OUTLIER_MEDIAN
    assert config.min_data_points == 3
    assert config.safety_margin_percent == pytest.approx(10.0)


def test_fallback_order_starts_with_the_preferred_window() -> None:
    """Rentang pilihan user selalu dicoba pertama."""
    assert PredictionConfig(preferred_window=WINDOW_30D).fallback_order == (
        WINDOW_30D,
        WINDOW_7D,
        WINDOW_24H,
    )


# --- status token (spec I.1) -------------------------------------------------


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (30.0, STATUS_NORMAL),
        (7.0, STATUS_WARNING),
        (5.0, STATUS_WARNING),
        (3.0, STATUS_CRITICAL),
        (2.0, STATUS_CRITICAL),
        (1.0, STATUS_VERY_CRITICAL),
        (0.2, STATUS_VERY_CRITICAL),
    ],
)
def test_status_levels_follow_day_thresholds(days: float, expected: str) -> None:
    """Tingkat kegentingan mengikuti ambang hari yang diatur user."""
    assert (
        determine_status(
            days_remaining=days, remaining_kwh=50.0, thresholds=TokenThresholds()
        )
        == expected
    )


def test_status_unknown_when_prediction_unavailable() -> None:
    """Tanpa prediksi, statusnya 'belum diketahui' - bukan 'aman'."""
    assert (
        determine_status(
            days_remaining=None, remaining_kwh=50.0, thresholds=TokenThresholds()
        )
        == STATUS_UNKNOWN
    )


def test_absolute_kwh_threshold_overrides_days() -> None:
    """Sisa kWh yang sangat sedikit langsung sangat kritis, berapa pun harinya."""
    thresholds = TokenThresholds(low_kwh=5.0)
    assert (
        determine_status(
            days_remaining=30.0, remaining_kwh=4.0, thresholds=thresholds
        )
        == STATUS_VERY_CRITICAL
    )
    assert (
        determine_status(
            days_remaining=30.0, remaining_kwh=6.0, thresholds=thresholds
        )
        == STATUS_NORMAL
    )


def test_kwh_threshold_works_without_prediction() -> None:
    """Ambang kWh tetap berguna walaupun prediksi belum tersedia."""
    assert (
        determine_status(
            days_remaining=None,
            remaining_kwh=2.0,
            thresholds=TokenThresholds(low_kwh=5.0),
        )
        == STATUS_VERY_CRITICAL
    )


def test_status_reports_hold_while_ledger_frozen() -> None:
    """Selama ledger dibekukan, status tidak berpura-pura tahu keadaan."""
    assert (
        determine_status(
            days_remaining=30.0,
            remaining_kwh=50.0,
            thresholds=TokenThresholds(),
            on_hold=True,
        )
        == STATUS_HOLD
    )


def test_custom_thresholds_are_honoured() -> None:
    """Ambang yang diubah user benar-benar dipakai."""
    thresholds = TokenThresholds(
        warning_days=14.0, critical_days=5.0, very_critical_days=2.0
    )
    assert (
        determine_status(
            days_remaining=10.0, remaining_kwh=50.0, thresholds=thresholds
        )
        == STATUS_WARNING
    )
    assert (
        determine_status(
            days_remaining=4.0, remaining_kwh=50.0, thresholds=thresholds
        )
        == STATUS_CRITICAL
    )
