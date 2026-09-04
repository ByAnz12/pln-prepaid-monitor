"""Test aritmetika ledger token - tanpa Home Assistant."""

from __future__ import annotations

import pytest

from custom_components.pln_prepaid_monitor.engines.token_engine import (
    HOLD_ACTION_ACCEPT,
    HOLD_ACTION_CALIBRATE,
    HOLD_ACTION_IGNORE,
    TokenLedger,
    TokenLedgerState,
)

NOW = "2026-09-03T10:00:00+07:00"


def _started_ledger(baseline: float = 1000.0, kwh: float = 50.0) -> TokenLedger:
    """Ledger yang sudah dimulai dengan satu pengisian."""
    ledger = TokenLedger()
    ledger.add_topup(kwh_credited=kwh, group_total=baseline, timestamp=NOW)
    return ledger


def test_first_topup_starts_the_ledger() -> None:
    """Pengisian pertama sekaligus jadi titik awal penghitungan."""
    ledger = TokenLedger()
    assert ledger.started is False

    ledger.add_topup(kwh_credited=50.0, group_total=1000.0, timestamp=NOW)

    assert ledger.started is True
    assert ledger.state.baseline_group_total == pytest.approx(1000.0)
    assert ledger.remaining_kwh(1000.0) == pytest.approx(50.0)
    assert ledger.consumed_kwh(1000.0) == pytest.approx(0.0)


def test_usage_reduces_remaining() -> None:
    """Pemakaian 12 kWh mengurangi sisa token sebanyak itu."""
    ledger = _started_ledger()
    assert ledger.remaining_kwh(1012.0) == pytest.approx(38.0)
    assert ledger.consumed_kwh(1012.0) == pytest.approx(12.0)


def test_topups_are_additive() -> None:
    """Isi ulang sebelum habis MENAMBAH ke sisa lama (spec G.2).

    Ini sudah dikonfirmasi user lewat pengecekan langsung ke layar meteran.
    """
    ledger = _started_ledger(baseline=1000.0, kwh=50.0)
    # Sudah terpakai 20 kWh, sisa 30.
    assert ledger.remaining_kwh(1020.0) == pytest.approx(30.0)

    ledger.add_topup(kwh_credited=40.0, group_total=1020.0, timestamp=NOW)

    # Sisa jadi 30 + 40 = 70, bukan 40.
    assert ledger.remaining_kwh(1020.0) == pytest.approx(70.0)
    assert ledger.total_credited_kwh == pytest.approx(90.0)


def test_multiple_topups_stack() -> None:
    """Beberapa kali isi ulang menumpuk dengan benar."""
    ledger = _started_ledger(baseline=1000.0, kwh=10.0)
    ledger.add_topup(kwh_credited=20.0, group_total=1005.0, timestamp=NOW)
    ledger.add_topup(kwh_credited=30.0, group_total=1010.0, timestamp=NOW)

    assert ledger.total_credited_kwh == pytest.approx(60.0)
    assert ledger.remaining_kwh(1015.0) == pytest.approx(45.0)


def test_remaining_can_go_negative_when_overdrawn() -> None:
    """Kalau pemakaian melampaui token, sisanya minus - bukan disembunyikan.

    Ini keadaan nyata saat listrik hampir padam dan meteran masuk masa tenggang;
    menyembunyikannya dengan membulatkan ke nol justru menyesatkan.
    """
    ledger = _started_ledger(baseline=1000.0, kwh=10.0)
    assert ledger.remaining_kwh(1015.0) == pytest.approx(-5.0)


def test_edit_topup_recomputes_total_from_history() -> None:
    """Salah ketik diperbaiki, totalnya ikut benar tanpa cache yang basi."""
    ledger = _started_ledger(baseline=1000.0, kwh=500.0)  # salah ketik: 500
    entry_id = ledger.active_topups[0]["id"]

    ledger.edit_topup(entry_id, kwh_credited=50.0)

    assert ledger.total_credited_kwh == pytest.approx(50.0)
    assert ledger.remaining_kwh(1000.0) == pytest.approx(50.0)


def test_edit_unknown_topup_returns_none() -> None:
    """Kode entri yang tidak ada dilaporkan, bukan diam-diam diabaikan."""
    ledger = _started_ledger()
    assert ledger.edit_topup("tidak-ada", kwh_credited=1.0) is None


def test_delete_topup_removes_it_from_total() -> None:
    """Entri yang tercatat dua kali bisa dihapus."""
    ledger = _started_ledger(baseline=1000.0, kwh=50.0)
    ledger.add_topup(kwh_credited=50.0, group_total=1000.0, timestamp=NOW)
    duplicate_id = ledger.active_topups[1]["id"]

    assert ledger.delete_topup(duplicate_id) is True
    assert ledger.total_credited_kwh == pytest.approx(50.0)
    assert ledger.delete_topup(duplicate_id) is False


def test_calibration_matches_the_physical_meter() -> None:
    """Kalibrasi menyamakan ledger dengan angka di layar meteran (spec G.3)."""
    ledger = _started_ledger(baseline=1000.0, kwh=50.0)
    # Sistem mengira sisa 40, tapi meteran menunjukkan 35.
    assert ledger.remaining_kwh(1010.0) == pytest.approx(40.0)

    ledger.calibrate(
        actual_remaining_kwh=35.0, group_total=1010.0, timestamp=NOW
    )

    assert ledger.remaining_kwh(1010.0) == pytest.approx(35.0)
    # Pemakaian berikutnya tetap mengurangi seperti biasa.
    assert ledger.remaining_kwh(1015.0) == pytest.approx(30.0)


def test_calibration_keeps_old_history_for_audit() -> None:
    """Riwayat lama tidak dihapus, hanya ditandai sudah digantikan."""
    ledger = _started_ledger(baseline=1000.0, kwh=50.0)
    ledger.calibrate(
        actual_remaining_kwh=35.0, group_total=1010.0, timestamp=NOW
    )

    assert len(ledger.state.entries) == 2
    assert ledger.state.entries[0]["superseded"] is True
    assert ledger.active_topups == []


def test_topup_after_calibration_adds_on_top() -> None:
    """Isi ulang sesudah kalibrasi menambah ke angka hasil kalibrasi."""
    ledger = _started_ledger(baseline=1000.0, kwh=50.0)
    ledger.calibrate(
        actual_remaining_kwh=35.0, group_total=1010.0, timestamp=NOW
    )
    ledger.add_topup(kwh_credited=20.0, group_total=1010.0, timestamp=NOW)

    assert ledger.remaining_kwh(1010.0) == pytest.approx(55.0)


def test_reset_starts_from_zero() -> None:
    """Reset penuh dipakai saat meteran fisik diganti (spec K.4)."""
    ledger = _started_ledger(baseline=1000.0, kwh=50.0)
    ledger.reset(group_total=1010.0, timestamp=NOW)

    assert ledger.total_credited_kwh == pytest.approx(0.0)
    assert ledger.remaining_kwh(1010.0) == pytest.approx(0.0)
    assert ledger.remaining_kwh(1015.0) == pytest.approx(-5.0)


def test_ledger_survives_restart() -> None:
    """Seluruh isi ledger bertahan lintas restart."""
    ledger = _started_ledger(baseline=1000.0, kwh=50.0)
    ledger.add_topup(kwh_credited=25.0, group_total=1010.0, timestamp=NOW)

    restored = TokenLedger(TokenLedgerState.from_dict(ledger.state.as_dict()))

    assert restored.total_credited_kwh == pytest.approx(75.0)
    assert restored.remaining_kwh(1010.0) == pytest.approx(65.0)
    assert len(restored.active_topups) == 2


def test_no_reading_means_no_answer() -> None:
    """Tanpa data energi, sisa token belum bisa dihitung."""
    ledger = TokenLedger()
    assert ledger.remaining_kwh(1000.0) is None
    assert ledger.consumed_kwh(None) is None


# --- penahanan ledger saat reset besar (D-007) -------------------------------


def test_hold_freezes_consumption() -> None:
    """Selama ditahan, sisa token tidak ikut terpotong oleh lonjakan."""
    ledger = _started_ledger(baseline=1000.0, kwh=50.0)
    assert ledger.remaining_kwh(1010.0) == pytest.approx(40.0)

    ledger.engage_hold(
        source_name="MCB TOKO",
        reset_from=1010.0,
        reset_to=15000.0,
        group_total=1010.0,
        timestamp=NOW,
    )

    assert ledger.on_hold is True
    # Total kelompok melonjak jauh, tapi sisa token dibekukan di angka lama.
    assert ledger.remaining_kwh(16010.0) == pytest.approx(40.0)


def test_hold_accept_counts_the_jump_as_usage() -> None:
    """Keputusan 'anggap pemakaian nyata' melanjutkan hitungan apa adanya."""
    ledger = _started_ledger(baseline=1000.0, kwh=50.0)
    ledger.engage_hold(
        source_name="MCB TOKO",
        reset_from=1010.0,
        reset_to=15000.0,
        group_total=1010.0,
        timestamp=NOW,
    )

    assert ledger.resolve_hold(
        action=HOLD_ACTION_ACCEPT, group_total=1030.0, timestamp=NOW
    )

    assert ledger.on_hold is False
    assert ledger.remaining_kwh(1030.0) == pytest.approx(20.0)


def test_hold_ignore_discards_the_jump() -> None:
    """Keputusan 'abaikan' membuat lonjakan tidak memotong sisa token."""
    ledger = _started_ledger(baseline=1000.0, kwh=50.0)
    ledger.engage_hold(
        source_name="MCB TOKO",
        reset_from=1010.0,
        reset_to=15000.0,
        group_total=1010.0,
        timestamp=NOW,
    )

    # Selama ditahan, total kelompok sudah melonjak 15.000 kWh.
    assert ledger.resolve_hold(
        action=HOLD_ACTION_IGNORE, group_total=16010.0, timestamp=NOW
    )

    assert ledger.on_hold is False
    # Sisa tetap 40, lonjakannya tidak pernah terhitung.
    assert ledger.remaining_kwh(16010.0) == pytest.approx(40.0)
    # Dan pemakaian sesudahnya kembali dihitung normal.
    assert ledger.remaining_kwh(16015.0) == pytest.approx(35.0)


def test_hold_calibrate_uses_the_meter_reading() -> None:
    """Keputusan 'kalibrasi' menyamakan ledger dengan angka meteran."""
    ledger = _started_ledger(baseline=1000.0, kwh=50.0)
    ledger.engage_hold(
        source_name="MCB TOKO",
        reset_from=1010.0,
        reset_to=15000.0,
        group_total=1010.0,
        timestamp=NOW,
    )

    assert ledger.resolve_hold(
        action=HOLD_ACTION_CALIBRATE,
        group_total=16010.0,
        timestamp=NOW,
        actual_remaining_kwh=12.5,
    )

    assert ledger.on_hold is False
    assert ledger.remaining_kwh(16010.0) == pytest.approx(12.5)


def test_calibrate_without_reading_is_refused() -> None:
    """Kalibrasi tanpa angka meteran tidak bisa dijalankan."""
    ledger = _started_ledger()
    ledger.engage_hold(
        source_name="MCB TOKO",
        reset_from=1010.0,
        reset_to=15000.0,
        group_total=1010.0,
        timestamp=NOW,
    )
    assert (
        ledger.resolve_hold(
            action=HOLD_ACTION_CALIBRATE, group_total=1010.0, timestamp=NOW
        )
        is False
    )
    assert ledger.on_hold is True


def test_resolving_without_hold_does_nothing() -> None:
    """Tidak ada penahanan berarti tidak ada yang perlu dilepas."""
    ledger = _started_ledger()
    assert (
        ledger.resolve_hold(
            action=HOLD_ACTION_ACCEPT, group_total=1000.0, timestamp=NOW
        )
        is False
    )


def test_second_hold_does_not_overwrite_the_first() -> None:
    """Penahanan yang sedang berjalan tidak tertimpa oleh kejadian berikutnya."""
    ledger = _started_ledger()
    ledger.engage_hold(
        source_name="MCB TOKO",
        reset_from=1010.0,
        reset_to=15000.0,
        group_total=1010.0,
        timestamp=NOW,
    )
    ledger.engage_hold(
        source_name="MCB RUMAH",
        reset_from=999.0,
        reset_to=8000.0,
        group_total=1020.0,
        timestamp="2026-09-04T10:00:00+07:00",
    )

    assert ledger.state.hold["source_name"] == "MCB TOKO"


def test_hold_survives_restart() -> None:
    """Penahanan tidak hilang kalau Home Assistant kebetulan restart."""
    ledger = _started_ledger(baseline=1000.0, kwh=50.0)
    ledger.engage_hold(
        source_name="MCB TOKO",
        reset_from=1010.0,
        reset_to=15000.0,
        group_total=1010.0,
        timestamp=NOW,
    )

    restored = TokenLedger(TokenLedgerState.from_dict(ledger.state.as_dict()))

    assert restored.on_hold is True
    assert restored.remaining_kwh(16010.0) == pytest.approx(40.0)
