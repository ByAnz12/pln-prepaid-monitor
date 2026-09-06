"""Tabel riwayat pemakaian yang bisa disaring dan diurutkan.

*Pure Python*, jadi seluruh penyaringan, pengelompokan, dan pengurutannya bisa
diuji tanpa Home Assistant dan tanpa database.

Kenapa kendalinya berupa entity, bukan tabel yang bisa diklik: kartu bawaan
Home Assistant tidak menjalankan kode di peramban, jadi tidak ada kartu tabel
yang judul kolomnya bisa diklik untuk mengurutkan. Yang bisa dilakukan tanpa
kartu HACS adalah menaruh kendalinya sebagai entity - persis seperti kotak
isian token yang sudah ada - lalu merender tabelnya dari atribut sensor.

Tiga sumbu yang bisa diatur user, dan bedanya sering tertukar:

* **jenis waktu** menentukan satuan *rentangnya* - "dari bulan Februari sampai
  Agustus";
* **tampilan** menentukan satuan *tiap barisnya* - "per hari";
* **urutan** menentukan bagaimana baris itu disusun.

Jadi jenis waktu bulan + tampilan hari berarti: pilih rentang dalam satuan
bulan, tapi tampilkan tiap harinya. Tampilan tidak pernah boleh lebih kasar
daripada jenis waktu - satu baris gabungan tidak menjawab pertanyaan apa pun.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

GRAIN_DAY = "day"
GRAIN_MONTH = "month"
GRAIN_YEAR = "year"

# Urut dari yang paling halus. Urutan ini yang dipakai untuk menolak tampilan
# yang lebih kasar daripada jenis waktunya.
GRAINS: tuple[str, ...] = (GRAIN_DAY, GRAIN_MONTH, GRAIN_YEAR)

SORT_TIME = "time"
SORT_KWH = "kwh"
SORT_COST = "cost"
SORTS: tuple[str, ...] = (SORT_TIME, SORT_KWH, SORT_COST)

DIRECTION_ASC = "asc"
DIRECTION_DESC = "desc"
DIRECTIONS: tuple[str, ...] = (DIRECTION_ASC, DIRECTION_DESC)

# Batas panjang tabel. Bukan selera: atribut state yang lebih besar dari 16 KiB
# ditolak recorder, dan tabel markdown 365 baris tidak terbaca oleh siapa pun.
DEFAULT_MAX_ROWS = 12
MAX_ROWS_LIMIT = 60

# Panjang batang perbandingan, dalam karakter blok. Batang adalah satu-satunya
# cara menggambar perbandingan di dalam kartu markdown.
BAR_WIDTH = 10


def finer_grains(scope: str) -> list[str]:
    """Tampilan yang masuk akal untuk satu jenis waktu.

    Tidak pernah lebih kasar daripada jenis waktunya. Jenis waktu "hari" dengan
    tampilan "tahun" hanya menghasilkan satu baris gabungan.
    """
    if scope not in GRAINS:
        return list(GRAINS)
    return list(GRAINS[: GRAINS.index(scope) + 1])


def clamp_view(scope: str, view: str) -> str:
    """Tampilan yang dipilih user, dikoreksi kalau terlalu kasar."""
    allowed = finer_grains(scope)
    return view if view in allowed else allowed[-1]


def bucket_key(moment: datetime, grain: str) -> tuple[int, ...]:
    """Penanda kelompok satu waktu, sesuai satuan tampilannya."""
    if grain == GRAIN_YEAR:
        return (moment.year,)
    if grain == GRAIN_MONTH:
        return (moment.year, moment.month)
    return (moment.year, moment.month, moment.day)


def range_bounds(scope: str, start: date, end: date) -> tuple[date, date]:
    """Rentang yang benar-benar dicakup, dilebarkan ke batas satuannya.

    User memilih "Februari sampai Agustus", dan yang dimaksud jelas: seluruh
    Februari sampai seluruh Agustus. Tanpa pelebaran ini, Agustus akan terpotong
    di tanggal 1.
    """
    if end < start:
        start, end = end, start

    if scope == GRAIN_YEAR:
        return date(start.year, 1, 1), date(end.year, 12, 31)
    if scope == GRAIN_MONTH:
        first = date(start.year, start.month, 1)
        last = _end_of_month(end)
        return first, last
    return start, end


def _end_of_month(day: date) -> date:
    """Hari terakhir bulan yang memuat ``day``."""
    if day.month == 12:
        return date(day.year, 12, 31)
    return date(day.year, day.month + 1, 1) - timedelta(days=1)


@dataclass
class UsageRow:
    """Satu baris tabel."""

    key: tuple[int, ...]
    label: str
    kwh: float = 0.0
    cost_rp: float | None = None

    def as_dict(self, number: int, peak_kwh: float) -> dict[str, Any]:
        """Bentuk yang dibaca template dashboard."""
        return {
            "no": number,
            "period": self.label,
            "kwh": round(self.kwh, 2),
            "cost_rp": None if self.cost_rp is None else round(self.cost_rp),
            "bar": bar(self.kwh, peak_kwh),
        }


def bar(value: float, peak: float) -> str:
    """Batang perbandingan dari karakter blok.

    Nilai yang bukan nol tidak pernah menghasilkan batang kosong: baris yang
    kelihatan tidak punya batang sama sekali terbaca seperti data yang hilang,
    padahal ia cuma kecil.
    """
    if peak <= 0 or value <= 0:
        return ""
    return "█" * max(1, round(value / peak * BAR_WIDTH))


@dataclass
class UsageQuery:
    """Seluruh pilihan user yang menentukan isi tabel."""

    scope: str = GRAIN_MONTH
    view: str = GRAIN_DAY
    sort: str = SORT_TIME
    direction: str = DIRECTION_DESC
    start: date | None = None
    end: date | None = None
    max_rows: int = DEFAULT_MAX_ROWS


@dataclass
class UsageTable:
    """Hasil jadi: baris yang tampil plus ringkasan seluruh rentangnya."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    total_kwh: float = 0.0
    total_cost_rp: float | None = None
    period_count: int = 0
    hidden_count: int = 0

    @property
    def empty(self) -> bool:
        """True kalau tidak ada satu pun periode pada rentang itu."""
        return self.period_count == 0


def build_table(
    *,
    query: UsageQuery,
    energy: list[tuple[datetime, float]],
    cost: list[tuple[datetime, float]] | None,
    labeller,
) -> UsageTable:
    """Susun tabel dari statistik harian.

    ``energy`` dan ``cost`` berupa ``(awal periode, nilai)`` dengan waktu
    **lokal** - sama seperti masukan ``period_summary``. Keduanya dikelompokkan
    ulang ke satuan tampilan lalu dipasangkan **berdasarkan penanda kelompok**,
    bukan berdasarkan posisi. Alasannya sama persis dengan D-055: daftar biaya
    bisa lebih pendek atau berlubang, dan memasangkannya per posisi membuat kWh
    dan Rupiah pada satu baris berasal dari periode yang berbeda.

    ``labeller`` mengubah penanda kelompok jadi teks yang dibaca user; ia
    disuntikkan dari luar supaya modul ini tetap bebas dari urusan bahasa.
    """
    view = clamp_view(query.scope, query.view)

    buckets: dict[tuple[int, ...], UsageRow] = {}
    for moment, value in energy:
        key = bucket_key(moment, view)
        row = buckets.get(key)
        if row is None:
            row = buckets[key] = UsageRow(key=key, label=labeller(key, view))
        row.kwh += value

    if cost is not None:
        by_key: dict[tuple[int, ...], float] = {}
        for moment, value in cost:
            by_key[bucket_key(moment, view)] = by_key.get(
                bucket_key(moment, view), 0.0
            ) + value
        for key, row in buckets.items():
            row.cost_rp = by_key.get(key)

    ordered = sorted(buckets.values(), key=lambda row: row.key)
    total_kwh = sum(row.kwh for row in ordered)
    costs = [row.cost_rp for row in ordered if row.cost_rp is not None]

    if query.sort == SORT_KWH:
        ordered.sort(key=lambda row: row.kwh)
    elif query.sort == SORT_COST:
        # Baris tanpa biaya selalu di bawah, apa pun arah urutannya - kosong
        # bukan berarti nol, dan menaruhnya di puncak daftar "terbesar" hanya
        # menyesatkan.
        ordered.sort(key=lambda row: (row.cost_rp is not None, row.cost_rp or 0.0))
    if query.direction == DIRECTION_DESC:
        ordered.reverse()

    limit = max(1, min(MAX_ROWS_LIMIT, int(query.max_rows or DEFAULT_MAX_ROWS)))
    shown = ordered[:limit]
    peak = max((row.kwh for row in shown), default=0.0)

    return UsageTable(
        rows=[row.as_dict(number, peak) for number, row in enumerate(shown, start=1)],
        total_kwh=round(total_kwh, 2),
        total_cost_rp=round(sum(costs)) if costs else None,
        period_count=len(ordered),
        hidden_count=max(0, len(ordered) - len(shown)),
    )
