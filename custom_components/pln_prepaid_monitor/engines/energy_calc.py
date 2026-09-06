"""Penjumlahan lintas sumber dan penghitung per periode.

Dua hal dikerjakan di sini, keduanya *pure Python* supaya bisa diuji terpisah:

1. **Total gabungan Billing Group** - menjumlahkan beberapa Energy Source jadi
   satu angka kWh yang tetap hanya-naik.
2. **Penghitung per periode** - "pemakaian jam ini / hari ini / minggu ini /
   bulan ini / tahun ini", yang di-reset di awal tiap siklus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .period import CycleConfig, cycle_start


@dataclass
class GroupTotalState:
    """State total gabungan, wajib bertahan lintas restart."""

    total: float | None = None
    member_last: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Bentuk yang disimpan ke .storage."""
        return {"total": self.total, "member_last": dict(self.member_last)}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> GroupTotalState:
        """Baca kembali dari .storage."""
        if not data:
            return cls()
        total = data.get("total")
        member_last = data.get("member_last") or {}
        return cls(
            total=None if total is None else float(total),
            member_last={
                str(key): float(value)
                for key, value in member_last.items()
                if value is not None
            },
        )


class GroupTotal:
    """Menjumlahkan beberapa sumber jadi satu angka kWh yang hanya-naik.

    Penjumlahan dilakukan **dari selisih tiap anggota**, bukan dari menjumlahkan
    nilai mentahnya setiap saat. Alasannya penting: kalau sebuah sumber baru
    ditambahkan ke grup (atau baru pertama kali mengirim data), angka mentahnya
    yang sudah besar - misalnya 15.114 kWh - tidak boleh tiba-tiba masuk sebagai
    pemakaian hari ini. Dengan menghitung selisih, riwayat lama anggota baru
    tidak pernah ikut terhitung.
    """

    def __init__(self, state: GroupTotalState | None = None) -> None:
        """Siapkan penjumlah grup."""
        self.state = state if state is not None else GroupTotalState()

    def update(self, member_values: dict[str, float | None]) -> float | None:
        """Perbarui total dari nilai kWh terkini tiap anggota."""
        state = self.state

        # Anggota yang sudah tidak jadi bagian grup dilupakan, supaya kalau nanti
        # dimasukkan lagi ia diperlakukan sebagai anggota baru.
        for stale in set(state.member_last) - set(member_values):
            del state.member_last[stale]

        available = {
            member_id: value
            for member_id, value in member_values.items()
            if value is not None
        }

        if state.total is None:
            if not available:
                return None
            # Nilai awal disamakan dengan jumlah pembacaan meteran saat ini,
            # supaya angka grup bisa dicocokkan dengan angka di meteran fisik.
            state.total = sum(available.values())
            state.member_last = dict(available)
            return state.total

        for member_id, value in available.items():
            previous = state.member_last.get(member_id)
            if previous is None:
                # Anggota baru bergabung: catat titik awalnya, jangan hitung
                # seluruh riwayat lamanya sebagai pemakaian.
                state.member_last[member_id] = value
                continue
            delta = value - previous
            if delta > 0:
                state.total += delta
            state.member_last[member_id] = value

        return state.total


@dataclass
class PeriodCounterState:
    """State satu penghitung periode."""

    cycle_start: str | None = None
    start_total: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Bentuk yang disimpan ke .storage."""
        return {"cycle_start": self.cycle_start, "start_total": self.start_total}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> PeriodCounterState:
        """Baca kembali dari .storage."""
        if not data:
            return cls()
        start_total = data.get("start_total")
        return cls(
            cycle_start=data.get("cycle_start"),
            start_total=None if start_total is None else float(start_total),
        )


class PeriodCounter:
    """Pemakaian sejak awal siklus berjalan, di-reset otomatis tiap siklus baru."""

    def __init__(
        self,
        period: str,
        config: CycleConfig,
        state: PeriodCounterState | None = None,
    ) -> None:
        """Siapkan penghitung untuk satu periode."""
        self.period = period
        self.config = config
        self.state = state if state is not None else PeriodCounterState()

    def sync(self, total: float | None, now: datetime) -> bool:
        """Selaraskan penghitung dengan total terkini. True bila siklus berganti.

        Dipanggil setiap kali total berubah **dan** tepat di setiap batas siklus.
        Pemeriksaan batas di sini juga yang menyelamatkan keadaan setelah Home
        Assistant mati melewati pergantian hari: begitu hidup lagi, siklus yang
        sudah lewat langsung ditutup, bukan dibiarkan menumpuk.
        """
        boundary = cycle_start(self.period, now, self.config)
        stored = self._stored_cycle_start()

        rolled_over = False
        if stored is None:
            # Pemasangan baru di tengah siklus. Kita tidak punya data sebelum
            # detik ini, jadi mengaku siklusnya mulai di batas resmi adalah
            # dusta - dan dusta yang mahal.
            #
            # Dipasang Sabtu siang, "Bulan ini" dulu mengaku ``last_reset``-nya
            # 1 September padahal angkanya cuma mencakup Sabtu siang ke sini.
            # Pembacanya wajar menyangka itu pemakaian sebulan berjalan, dan
            # tidak ada satu pun tanda bahwa sebagian besar bulan itu hilang.
            #
            # Home Assistant sendiri memakai ``last_reset`` untuk menafsirkan
            # penurunan pada sensor ``total``, jadi tanggal yang mengada-ada
            # bukan cuma menyesatkan pembaca - ia bisa merusak statistik
            # jangka panjang milik HA.
            self.state.cycle_start = now.isoformat()
            self.state.start_total = total
        elif stored < boundary:
            self.state.cycle_start = boundary.isoformat()
            self.state.start_total = total
            rolled_over = True
        elif self.state.start_total is None:
            self.state.start_total = total

        return rolled_over

    def covers_full_cycle(self, now: datetime) -> bool:
        """True bila penghitung ini sudah mencakup siklusnya dari awal.

        False hanya pada siklus pertama sesudah pemasangan, dan itulah satu-
        satunya saat angkanya tidak boleh dibandingkan dengan siklus lain.
        """
        stored = self._stored_cycle_start()
        if stored is None:
            return False
        return stored <= cycle_start(self.period, now, self.config)

    def _stored_cycle_start(self) -> datetime | None:
        """Awal siklus yang tersimpan, atau None kalau belum pernah diset."""
        if not self.state.cycle_start:
            return None
        try:
            return datetime.fromisoformat(self.state.cycle_start)
        except ValueError:
            return None

    @property
    def cycle_start_at(self) -> datetime | None:
        """Awal siklus berjalan, dipakai entity sebagai ``last_reset``."""
        return self._stored_cycle_start()

    def value(self, total: float | None) -> float | None:
        """Pemakaian sejak awal siklus, atau None bila belum ada data."""
        if total is None or self.state.start_total is None:
            return None
        # Guard terhadap urutan pemanggilan yang tidak terduga: penghitung
        # periode tidak pernah boleh negatif.
        return max(0.0, total - self.state.start_total)
