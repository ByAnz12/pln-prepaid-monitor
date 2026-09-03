"""Test aritmetika biaya - tanpa Home Assistant."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.pln_prepaid_monitor.engines.cost_engine import (
    CostAccumulator,
    CostTotalState,
    TariffConfig,
    append_rate_version,
    apply_rounding,
    fixed_charge_accrued,
)

JAKARTA = ZoneInfo("Asia/Jakarta")
RATE = 1444.70


def _at(text: str) -> datetime:
    """Waktu lokal Jakarta dari string."""
    return datetime.fromisoformat(text).replace(tzinfo=JAKARTA)


def test_cost_is_energy_times_rate() -> None:
    """Pemakaian 10 kWh pada tarif Rp 1.444,70 = Rp 14.447."""
    cost = CostAccumulator()
    cost.update(100.0, RATE)
    total = cost.update(110.0, RATE)
    assert total == pytest.approx(14447.0)


def test_first_reading_costs_nothing() -> None:
    """Pembacaan pertama cuma jadi titik awal, belum ada yang ditagihkan."""
    cost = CostAccumulator()
    assert cost.update(15498.27, RATE) == pytest.approx(0.0)


def test_tariff_change_is_not_applied_retroactively() -> None:
    """Kenaikan tarif tidak mengubah biaya yang sudah tercatat (spec K.7).

    Ini inti perilaku yang benar secara akuntansi: 10 kWh yang dipakai pada
    tarif lama tetap dihitung dengan tarif lama, walaupun tarifnya naik besok.
    """
    cost = CostAccumulator()
    cost.update(100.0, 1000.0)
    cost.update(110.0, 1000.0)  # 10 kWh @ Rp 1.000 = Rp 10.000

    # Tarif naik jadi Rp 1.500, lalu pakai 10 kWh lagi.
    total = cost.update(120.0, 1500.0)

    assert total == pytest.approx(10000.0 + 15000.0)
    # Bukan (110-100+120-110) * 1500 = 30.000.
    assert total != pytest.approx(30000.0)


def test_energy_that_does_not_move_costs_nothing() -> None:
    """Tanpa pemakaian baru, biaya tidak bertambah."""
    cost = CostAccumulator()
    cost.update(100.0, RATE)
    cost.update(110.0, RATE)
    before = cost.state.total_rp
    assert cost.update(110.0, RATE) == pytest.approx(before)


def test_cost_never_decreases_on_backward_reading() -> None:
    """Pembacaan mundur tidak boleh mengurangi biaya yang sudah tercatat."""
    cost = CostAccumulator()
    cost.update(100.0, RATE)
    cost.update(110.0, RATE)
    before = cost.state.total_rp
    total = cost.update(105.0, RATE)
    assert total == pytest.approx(before)


def test_cost_survives_restart() -> None:
    """Total biaya dan titik acuan energi bertahan lintas restart."""
    cost = CostAccumulator()
    cost.update(100.0, RATE)
    cost.update(110.0, RATE)

    restored = CostAccumulator(CostTotalState.from_dict(cost.state.as_dict()))
    total = restored.update(120.0, RATE)
    assert total == pytest.approx(2 * 10 * RATE)


def test_no_energy_data_means_no_cost() -> None:
    """Sebelum ada data energi, biaya belum punya angka."""
    cost = CostAccumulator()
    assert cost.update(None, RATE) is None


# --- pembulatan --------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("nearest", 14447.0), ("up", 14448.0), ("down", 14447.0)],
)
def test_rounding_modes(mode: str, expected: float) -> None:
    """Tiga cara pembulatan ke rupiah terdekat."""
    tariff = TariffConfig(rate_rp_per_kwh=RATE, rounding_mode=mode)
    assert apply_rounding(14447.4, tariff) == pytest.approx(expected)


def test_rounding_to_hundreds() -> None:
    """Pembulatan ke kelipatan selain 1 rupiah."""
    tariff = TariffConfig(
        rate_rp_per_kwh=RATE, rounding_mode="nearest", rounding_unit_rp=100
    )
    assert apply_rounding(14447.0, tariff) == pytest.approx(14400.0)


def test_rounding_can_be_switched_off() -> None:
    """Kelipatan 0 berarti tidak dibulatkan sama sekali."""
    tariff = TariffConfig(rate_rp_per_kwh=RATE, rounding_unit_rp=0)
    assert apply_rounding(14447.437, tariff) == pytest.approx(14447.437)


def test_rounding_keeps_values_non_decreasing() -> None:
    """Angka yang naik tetap naik setelah dibulatkan - tidak pernah mundur."""
    tariff = TariffConfig(rate_rp_per_kwh=RATE, rounding_unit_rp=100)
    previous = None
    for raw in range(0, 5000, 37):
        value = apply_rounding(float(raw), tariff)
        if previous is not None:
            assert value >= previous
        previous = value


def test_rounding_ignores_missing_value() -> None:
    """Tidak ada angka berarti tidak ada yang dibulatkan."""
    assert apply_rounding(None, TariffConfig(rate_rp_per_kwh=RATE)) is None


# --- biaya beban -------------------------------------------------------------


def test_no_fixed_charge_by_default() -> None:
    """PLN prabayar rumah tangga umumnya tidak punya biaya beban."""
    tariff = TariffConfig(rate_rp_per_kwh=RATE)
    assert tariff.fixed_charge_per_day == pytest.approx(0.0)
    assert fixed_charge_accrued(
        tariff, _at("2026-09-01 00:00:00"), _at("2026-09-15 00:00:00")
    ) == pytest.approx(0.0)


def test_monthly_fixed_charge_accrues_daily() -> None:
    """Biaya beban bulanan disebar per hari, tidak melompat di tanggal 1."""
    tariff = TariffConfig(
        rate_rp_per_kwh=RATE, fixed_charge_rp=30000.0, fixed_charge_period="monthly"
    )
    per_day = tariff.fixed_charge_per_day
    assert per_day == pytest.approx(30000.0 / (365.25 / 12))

    accrued = fixed_charge_accrued(
        tariff, _at("2026-09-01 00:00:00"), _at("2026-09-11 00:00:00")
    )
    assert accrued == pytest.approx(per_day * 10)


def test_daily_fixed_charge() -> None:
    """Biaya beban harian dihitung apa adanya per hari."""
    tariff = TariffConfig(
        rate_rp_per_kwh=RATE, fixed_charge_rp=500.0, fixed_charge_period="daily"
    )
    assert tariff.fixed_charge_per_day == pytest.approx(500.0)
    accrued = fixed_charge_accrued(
        tariff,
        _at("2026-09-03 00:00:00"),
        _at("2026-09-03 00:00:00") + timedelta(hours=12),
    )
    assert accrued == pytest.approx(250.0)


def test_fixed_charge_without_cycle_start_is_zero() -> None:
    """Tanpa titik awal siklus, tidak ada yang bisa dihitung."""
    tariff = TariffConfig(rate_rp_per_kwh=RATE, fixed_charge_rp=30000.0)
    assert fixed_charge_accrued(tariff, None, _at("2026-09-11 00:00:00")) == 0.0


# --- konfigurasi tarif -------------------------------------------------------


def test_tariff_from_dict_uses_safe_defaults() -> None:
    """Isian rusak tidak boleh membuat integrasi gagal dimuat."""
    tariff = TariffConfig.from_dict(
        {
            "rate_rp_per_kwh": "bukan angka",
            "fixed_charge_rp": None,
            "fixed_charge_period": "mingguan",
            "rounding_mode": "acak",
            "rounding_unit_rp": "x",
        }
    )
    assert tariff.rate_rp_per_kwh == 0.0
    assert tariff.fixed_charge_rp == 0.0
    assert tariff.fixed_charge_period == "monthly"
    assert tariff.rounding_mode == "nearest"
    assert tariff.rounding_unit_rp == 1.0


def test_negative_rate_is_clamped_to_zero() -> None:
    """Tarif negatif tidak masuk akal dan tidak boleh dipakai."""
    assert TariffConfig.from_dict({"rate_rp_per_kwh": -500}).rate_rp_per_kwh == 0.0


# --- riwayat versi tarif (spec K.7) ------------------------------------------


def test_rate_history_appends_new_version() -> None:
    """Perubahan tarif menambah versi baru, tidak menimpa yang lama."""
    history = append_rate_version(None, 1444.70, "2026-01-01T00:00:00+07:00")
    history = append_rate_version(history, 1600.00, "2026-09-03T10:00:00+07:00")

    assert len(history) == 2
    assert history[0]["rate_rp_per_kwh"] == pytest.approx(1444.70)
    assert history[0]["effective_from"] == "2026-01-01T00:00:00+07:00"
    assert history[1]["rate_rp_per_kwh"] == pytest.approx(1600.00)


def test_rate_history_ignores_unchanged_rate() -> None:
    """Menyimpan ulang tanpa mengubah tarif tidak menambah versi palsu."""
    history = append_rate_version(None, 1444.70, "2026-01-01T00:00:00+07:00")
    history = append_rate_version(history, 1444.70, "2026-09-03T10:00:00+07:00")
    assert len(history) == 1
