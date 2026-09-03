"""Test akumulator aman-reset terhadap perilaku resmi Home Assistant Core.

Vektor uji di ``test_matches_core_total_increasing_vector`` dan
``test_matches_core_small_dip_vector`` **disalin langsung** dari test resmi HA
Core 2026.8.3:

* ``tests/components/sensor/test_recorder.py::
  test_compile_hourly_sum_statistics_total_increasing``
  -> ``seq = [10, 15, 20, 10, 30, 40, 50, 60, 70]``, ``sum`` di akhir tiga
  periode berturut-turut adalah ``10``, ``50``, ``80``.
* ``...::test_compile_hourly_sum_statistics_total_increasing_small_dip``
  -> ``seq = [10, 15, 20, 19, 30, 40, 39, 60, 70]``, ``sum`` = ``10``, ``30``,
  ``60``.

Kalau implementasi kita menyimpang dari angka-angka ini, artinya angka kita
akan berbeda dari statistics bawaan Home Assistant untuk sensor yang sama -
dan itu bug, bukan pilihan desain.
"""

from __future__ import annotations

import pytest

from custom_components.pln_prepaid_monitor.engines.accumulator import (
    AccumulatorEvent,
    AccumulatorState,
    IntegratorState,
    PowerIntegrator,
    ResetSafeAccumulator,
)


def _consume(sequence: list[float]) -> ResetSafeAccumulator:
    """Jalankan satu deret pembacaan lewat akumulator."""
    accumulator = ResetSafeAccumulator(seed_offset=False)
    for value in sequence:
        accumulator.update(value)
    return accumulator


def test_matches_core_total_increasing_vector() -> None:
    """Vektor resmi HA Core dengan satu reset di tengah."""
    sequence = [10, 15, 20, 10, 30, 40, 50, 60, 70]
    accumulator = ResetSafeAccumulator(seed_offset=False)

    # Periode 1: pembacaan ke-0 sampai ke-2 -> sum 10
    for value in sequence[0:3]:
        accumulator.update(value)
    assert accumulator.state.consumed == pytest.approx(10.0)

    # Periode 2: pembacaan ke-3 sampai ke-5, termasuk reset 20 -> 10 -> sum 50
    for value in sequence[3:6]:
        accumulator.update(value)
    assert accumulator.state.consumed == pytest.approx(50.0)

    # Periode 3: pembacaan ke-6 sampai ke-8 -> sum 80
    for value in sequence[6:9]:
        accumulator.update(value)
    assert accumulator.state.consumed == pytest.approx(80.0)

    assert accumulator.state.resets_detected == 1


def test_matches_core_small_dip_vector() -> None:
    """Vektor resmi HA Core: turun <=10% bukan reset."""
    sequence = [10, 15, 20, 19, 30, 40, 39, 60, 70]
    accumulator = ResetSafeAccumulator(seed_offset=False)

    for value in sequence[0:3]:
        accumulator.update(value)
    assert accumulator.state.consumed == pytest.approx(10.0)

    for value in sequence[3:6]:
        accumulator.update(value)
    assert accumulator.state.consumed == pytest.approx(30.0)

    for value in sequence[6:9]:
        accumulator.update(value)
    assert accumulator.state.consumed == pytest.approx(60.0)

    assert accumulator.state.resets_detected == 0
    assert accumulator.state.dips_detected == 2


def test_reset_threshold_is_exactly_ten_percent() -> None:
    """Tepat 90% dianggap dip; di bawahnya baru reset."""
    accumulator = ResetSafeAccumulator(seed_offset=False)
    accumulator.update(100.0)
    assert accumulator.update(90.0) is AccumulatorEvent.DIP
    assert accumulator.state.resets_detected == 0

    accumulator = ResetSafeAccumulator(seed_offset=False)
    accumulator.update(100.0)
    assert accumulator.update(89.9) is AccumulatorEvent.RESET
    assert accumulator.state.resets_detected == 1


def test_negative_reading_never_counted() -> None:
    """Nilai negatif dibuang total, tidak jadi konsumsi maupun reset."""
    accumulator = ResetSafeAccumulator(seed_offset=False)
    accumulator.update(10.0)
    accumulator.update(20.0)
    assert accumulator.update(-5.0) is AccumulatorEvent.NEGATIVE_IGNORED
    assert accumulator.state.consumed == pytest.approx(10.0)
    assert accumulator.state.resets_detected == 0
    assert accumulator.state.negatives_ignored == 1

    # Pembacaan sehat berikutnya lanjut dari titik terakhir yang sah.
    accumulator.update(25.0)
    assert accumulator.state.consumed == pytest.approx(15.0)


def test_non_numeric_reading_ignored() -> None:
    """State non-angka tidak boleh merusak akumulasi."""
    accumulator = ResetSafeAccumulator(seed_offset=False)
    accumulator.update(10.0)
    assert accumulator.update("unavailable") is AccumulatorEvent.INVALID_IGNORED
    assert accumulator.update(None) is AccumulatorEvent.INVALID_IGNORED
    accumulator.update(12.0)
    assert accumulator.state.consumed == pytest.approx(2.0)


def test_never_decreases_across_reset() -> None:
    """Nilai yang dipublikasikan tidak pernah turun saat counter fisik reset."""
    accumulator = ResetSafeAccumulator(seed_offset=True)
    accumulator.update(21507.97)
    before = accumulator.state.total
    accumulator.update(21510.00)
    accumulator.update(0.5)  # meter diganti/reset firmware
    after = accumulator.state.total

    assert before == pytest.approx(21507.97)
    assert after is not None
    assert after >= before
    assert after == pytest.approx(21510.50)


def test_seed_offset_mirrors_physical_meter() -> None:
    """Pembacaan pertama jadi offset supaya angkanya mirip meter fisik."""
    accumulator = ResetSafeAccumulator(seed_offset=True)
    accumulator.update(15498.27)
    assert accumulator.state.total == pytest.approx(15498.27)
    assert accumulator.state.consumed == pytest.approx(0.0)

    accumulator.update(15500.27)
    assert accumulator.state.total == pytest.approx(15500.27)
    assert accumulator.state.consumed == pytest.approx(2.0)


def test_state_survives_serialisation_round_trip() -> None:
    """State akumulator harus utuh setelah disimpan dan dibaca ulang."""
    accumulator = _consume([10, 15, 20, 10, 30])
    restored = AccumulatorState.from_dict(accumulator.state.as_dict())

    assert restored.consumed == pytest.approx(accumulator.state.consumed)
    assert restored.raw_prev == accumulator.state.raw_prev
    assert restored.zero_point == accumulator.state.zero_point
    assert restored.banked == accumulator.state.banked
    assert restored.resets_detected == accumulator.state.resets_detected

    # Lanjut menghitung setelah restore harus sama dengan tanpa restore.
    resumed = ResetSafeAccumulator(restored, seed_offset=False)
    resumed.update(40.0)
    accumulator.update(40.0)
    assert resumed.state.consumed == pytest.approx(accumulator.state.consumed)


def test_from_dict_tolerates_missing_keys() -> None:
    """Data .storage yang tidak lengkap tidak boleh membuat integrasi gagal."""
    assert AccumulatorState.from_dict(None).raw_prev is None
    assert AccumulatorState.from_dict({}).consumed == 0.0
    assert AccumulatorState.from_dict({"banked": 5}).consumed == 0.0


def test_power_integrator_left_riemann() -> None:
    """Integrasi daya memakai daya sebelumnya sepanjang interval."""
    integrator = PowerIntegrator()
    integrator.add_sample(1000.0, 0.0)  # 1000 W
    total = integrator.add_sample(2000.0, 3600.0)  # satu jam kemudian
    assert total == pytest.approx(1.0)  # 1000 W selama 1 jam = 1 kWh

    total = integrator.add_sample(0.0, 7200.0)  # 2000 W selama 1 jam
    assert total == pytest.approx(3.0)


def test_power_integrator_does_not_backfill_gaps() -> None:
    """Setelah sumber hilang, jeda tidak diisi mundur."""
    integrator = PowerIntegrator()
    integrator.add_sample(1000.0, 0.0)
    integrator.pause()
    total = integrator.add_sample(1000.0, 36000.0)  # kembali 10 jam kemudian
    assert total == pytest.approx(0.0)


def test_integrator_state_round_trip() -> None:
    """State integrator juga harus selamat dari restart."""
    integrator = PowerIntegrator()
    integrator.add_sample(1000.0, 0.0)
    integrator.add_sample(1000.0, 3600.0)
    restored = IntegratorState.from_dict(integrator.state.as_dict())
    assert restored.total_kwh == pytest.approx(1.0)
    assert restored.last_power_w == pytest.approx(1000.0)
    assert restored.last_timestamp == pytest.approx(3600.0)
