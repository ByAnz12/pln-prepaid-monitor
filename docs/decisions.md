# Catatan keputusan implementasi

Dokumen ini mencatat keputusan yang diambil **saat implementasi**, di luar apa
yang sudah tertulis di [`spec.md`](spec.md) - termasuk satu koreksi terhadap
spec itu sendiri. Urut dari yang terbaru.

Label kepercayaan mengikuti konvensi yang sama dengan `spec.md`
(VERIFIED / LIKELY / UNKNOWN / ASUMSI DESAIN).

---

## D-011 · Billing Group menerima beberapa sumber langsung; objek Aggregate ditunda

**Tanggal**: 3 September 2026 · **Milestone 2** · **Menyimpang dari spec D.1**

Spec mendefinisikan **Aggregate** sebagai objek tersendiri (punya nama, punya
daftar anggota, bisa bersarang), lalu Billing Group mengikat **satu** measurement
yang boleh berupa Source atau Aggregate.

Yang diimplementasikan: **Billing Group langsung menerima satu atau beberapa
Energy Source**. Kemampuannya sama - beberapa meteran bisa dijumlahkan jadi satu
tagihan - tapi tanpa memperkenalkan satu konsep tambahan ke user.

Alasannya:

- Aggregate tidak menghasilkan entity apa pun (spec D.2 hanya mendaftar entity
  per Billing Group), jadi bagi user ia tak terlihat kecuali sebagai pilihan
  tambahan di form. Satu konsep abstrak lebih banyak, nol hal baru yang terlihat.
- Nilai tambahnya baru terasa kalau **penjumlahan yang sama dipakai ulang di
  beberapa Billing Group** atau perlu bersarang. Untuk susunan PLN_HOME /
  PLN_TOKO milik user, keduanya tidak terjadi.
- Menambahkannya nanti tidak butuh migrasi data: Billing Group tinggal menerima
  id Aggregate berdampingan dengan id Source di field yang sama.

**Kalau user memang ingin objek Aggregate bernama, ini bisa ditambahkan sebagai
milestone kecil tersendiri** - bukan keputusan final.

---

## D-010 · Grup tidak membuat sensor tegangan, arus, dan frekuensi

**Tanggal**: 3 September 2026 · **Milestone 2**

Spec D.2 mendaftar `voltage`, `current`, dan `frequency` di tingkat Billing
Group. Yang dibuat hanya **energi** dan **daya**.

Alasannya fisika, bukan kemalasan: menjumlahkan atau merata-ratakan tegangan
dari dua meteran yang berada di rangkaian berbeda tidak berarti apa-apa. Daya
dan energi memang penjumlahan yang sah; tegangan bukan. Membuat sensor
"tegangan gabungan" berarti mengarang angka yang kelihatan resmi padahal tidak
mewakili apa pun.

Untuk grup beranggota satu sumber - yaitu kasus PLN_HOME dan PLN_TOKO - sensor
tegangan/arus/frekuensi milik sumber itu sendiri sudah ada dan bisa langsung
dipakai di dashboard.

---

## D-009 · Total grup dihitung dari selisih anggota, bukan penjumlahan mentah

**Tanggal**: 3 September 2026 · **Milestone 2**

Total Billing Group tidak dihitung sebagai "jumlahkan angka semua anggota setiap
saat", melainkan sebagai akumulasi **selisih** tiap anggota.

Bedanya terasa persis saat konfigurasi berubah: kalau MCB TOKO (angka meteran
15.114 kWh) dimasukkan ke grup yang sudah berjalan, penjumlahan mentah akan
membuat total grup melonjak 15.114 kWh seketika - dan penghitung "pemakaian hari
ini" ikut melonjak sebesar itu. Dengan menghitung selisih, riwayat lama anggota
baru tidak pernah ikut terhitung; yang dihitung hanya pemakaian sejak ia
bergabung.

Hal yang sama melindungi kasus anggota yang mati lalu hidup lagi.

---

## D-008 · MCB TOKO tidak mengekspos token bawaannya ke Home Assistant

**Tanggal**: 3 September 2026 · **Sumber**: keterangan langsung user

Sisa token bawaan MCB TOKO **hanya terlihat di aplikasi Smart Life**, tidak ada
entity-nya di Home Assistant.

Konsekuensi: gagasan kanal pembanding read-only di Milestone 4 (menampilkan
angka token bawaan perangkat berdampingan dengan hitungan kita) **dibatalkan** -
tidak ada datanya untuk dibaca. Rancangan Token Engine tidak terpengaruh, karena
memang sejak awal tidak bergantung padanya (lihat [D-005](#d-005--latar-belakang-sebenarnya-token-bawaan-tuya-di-mcb-toko-ter-reset-sendiri)).

Perbandingan tetap bisa dilakukan user secara manual: buka Smart Life, bandingkan
dengan sensor sisa token buatan kita nanti.

---

## D-005 · Latar belakang sebenarnya: token bawaan Tuya di MCB TOKO ter-reset sendiri

**Tanggal**: 3 September 2026 · **Sumber**: keterangan langsung user

MCB TOKO (via Tuya) sebenarnya **sudah punya fitur token bawaan**. Beberapa
bulan terakhir token itu **ter-reset mendadak** tanpa sebab yang diketahui -
belum jelas apakah MCB-nya bermasalah, integrasinya, atau cloud Tuya. Inilah
alasan utama proyek ini dibuat: **sistem pencatatan token yang berdiri sendiri
dan tidak ikut rusak ketika fitur bawaan perangkat rusak.**

Konsekuensi desain:

1. Ledger token kita **tidak boleh** pernah membaca atau bergantung pada nilai
   token bawaan perangkat. Sumber kebenaran kita hanya kWh kumulatif + catatan
   top-up manual. Ini sudah sesuai desain spec Bagian G, sekarang jadi punya
   alasan yang konkret, bukan sekadar prinsip.
2. Yang belum diketahui dan **penting untuk dijawab**: apakah yang ter-reset
   hanya penghitung token, atau **sensor energi kumulatifnya juga**? Kalau hanya
   token, sistem kita tidak terpengaruh sama sekali. Kalau sensor energinya juga
   ikut reset, akumulator kita yang menanganinya - dan itu perlu diverifikasi
   dengan data nyata, bukan diasumsikan.
3. Karena itu D-006 ditambahkan ke Milestone 1.

**Status**: UNKNOWN (penyebab reset) - akan terjawab secara empiris setelah M1
berjalan beberapa waktu di instalasi user.

---

## D-006 · Akumulator mencatat detail setiap reset, bukan cuma menghitungnya

**Tanggal**: 3 September 2026 · **Milestone 1** · **Konsekuensi dari D-005**

Semula akumulator hanya menyimpan `resets_detected` (angka hitungan) dan menulis
satu baris log. Itu cukup untuk kebenaran perhitungan, tapi **tidak cukup untuk
diagnosis** - padahal diagnosis adalah kebutuhan asli user (D-005).

Ditambahkan tiga keterangan yang ikut tersimpan lintas restart:

| Atribut entity | Isi |
|---|---|
| `last_reset_detected_at` | Kapan reset terakhir terjadi (ISO 8601 UTC) |
| `last_reset_from_kwh` | Pembacaan terakhir sebelum jatuh |
| `last_reset_to_kwh` | Pembacaan pertama sesudah jatuh |

Dengan ini user bisa membuka **Developer Tools -> States** pada
`sensor.mcb_toko_energy` dan langsung tahu apakah sensor energinya ikut
ter-reset, kapan, dan sebesar apa - tanpa mengaduk-aduk log.

---

## D-004 · Sampler daya berkala untuk sumber tanpa sensor kWh

**Tanggal**: 3 September 2026 · **Milestone 1** · **Ditemukan oleh test**

Home Assistant hanya mengirim event `state_changed` kalau nilainya **berubah**.
Sensor daya yang melaporkan angka sama berulang kali (beban stabil) tidak
memicu event apa pun, sehingga integrasi Riemann berhenti diam-diam.

Ditambahkan sampler `async_track_time_interval` setiap 30 detik, **hanya** untuk
sumber yang tidak punya sensor kWh. Sumber dengan sensor kWh asli tidak
terpengaruh sama sekali.

---

## D-003 · Pseudocode G.1 di spec dikoreksi: yang diikuti adalah F.2 / HA Core

**Tanggal**: 3 September 2026 · **Milestone 1** · **Status: VERIFIED**

Spec meminta implementasi mengikuti pseudocode di G.1 secara persis. Setelah
dijalankan angkanya, pseudocode itu **berbeda hasil** dari algoritma HA Core
yang sudah diverifikasi di F.2, karena dua kesalahan yang kebetulan saling
menutupi pada vektor uji resmi:

1. Selisih tiap langkah sudah ditambahkan ke `consumed`, lalu **ditambahkan
   sekali lagi** sebagai `(raw_prev − zero_point)` saat reset (dobel hitung).
2. Pembacaan pertama **sesudah** reset tidak pernah dihitung.

| Kasus | HA Core / F.2 | Pseudocode G.1 |
|---|---|---|
| Vektor resmi HA (20 → 10) | 20 | 20 (kebetulan sama) |
| Meteran nyata (21.507 → 21.600 → reset ke 0,5) | **93,5 kWh** | **186 kWh** |

Yang diimplementasikan adalah versi HA Core:

```
consumed = banked + (raw_prev − zero_point)
```

dengan `banked` hanya bertambah saat reset genuine. Diuji langsung terhadap
vektor test resmi HA Core `test_compile_hourly_sum_statistics_total_increasing`
dan `..._small_dip` di `tests/test_accumulator.py`.

**Catatan**: `spec.md` Bagian G.1 sebaiknya diperbarui mengikuti ini.

---

## D-007 · Pengaman ledger token saat reset besar (rancangan Milestone 4)

**Tanggal**: 3 September 2026 · **Disetujui user** · **Belum diimplementasikan**

**Masalah**: karena kita mengikuti HA Core (D-003), pembacaan pertama sesudah
reset dihitung penuh sebagai konsumsi. Untuk reset firmware biasa (jatuh ke
0,5 kWh) dampaknya bisa diabaikan. Tapi kalau meteran **diganti** dan meteran
baru sudah menunjukkan angka besar, seluruh angka itu langsung terhitung sebagai
konsumsi - sisa token bisa terbaca habis seketika dan memicu notifikasi
VERY_CRITICAL palsu.

**Yang menentukan besar-kecilnya risiko** bukan seberapa dalam angkanya jatuh,
melainkan **berapa nilai pembacaan pertama sesudah reset** (`last_reset_to_kwh`).

**Rancangan**:

1. Ambang `reset_hold_threshold_kwh`, configurable, usulan default **1,0 kWh**.
2. Kalau `last_reset_to_kwh` melebihi ambang itu, **ledger token dibekukan**:
   `consumed_since_baseline_kwh` berhenti bertambah, `token_status` jadi `hold`.
3. User diberi tahu lewat dua jalur sekaligus: kartu **Repairs** di Home
   Assistant, dan pesan Telegram berawalan `[Token PLN]`.
4. User memilih salah satu:
   - **Anggap konsumsi nyata** - ledger lanjut, angka itu dipotong dari token.
   - **Abaikan (meteran diganti/reset)** - pembacaan pasca-reset jadi titik awal
     baru, tidak dipotong dari token.
   - **Kalibrasi** - user memasukkan angka yang tertera di layar meteran fisik,
     ledger disamakan dengan itu (`calibrate_token_reading`, spec G.3).
5. **Yang dibekukan hanya ledger token.** `sensor.<sumber>_energy` dan long-term
   statistics tetap berjalan normal mengikuti aturan HA Core, supaya angka kita
   tidak pernah bertentangan dengan statistik bawaan Home Assistant. Pemisahan
   inilah alasan pengaman ini ditempatkan di Token Engine, bukan di akumulator.

**Yang masih perlu diverifikasi saat M4**: API `issue_registry` / Repairs pada
Core 2026.8.3 - ditandai **LIKELY**, belum dicek ke source seperti API subentry.

---

## D-002 · Nilai awal akumulator disamakan dengan "total forward energy"

**Tanggal**: 3 September 2026 · **Milestone 1** · **Diputuskan user**

Pembacaan mentah pertama dipakai sebagai `offset`, sehingga
`sensor.<sumber>_energy` menampilkan angka yang sama dengan *total forward
energy* di aplikasi Smart Life. Tujuannya supaya user punya cara mencocokkan
angka secara langsung.

Sesudah reset, angka kita **terus naik** sementara angka aplikasi kembali dari
nol. Atribut `source_raw_value` menyimpan angka mentah apa adanya, jadi
pencocokan tetap bisa dilakukan kapan saja.

---

## D-001 · Satu entry induk + config subentry, bukan options flow bergaya menu

**Tanggal**: 3 September 2026 · **Milestone 1** · **Status: VERIFIED**

Diverifikasi langsung ke `homeassistant-2026.8.3-py3-none-any.whl` sebelum
dipakai:

| Yang dipakai | Lokasi di source 2026.8.3 |
|---|---|
| `ConfigSubentry`, `ConfigSubentryFlow` | `config_entries.py:371`, `:3732` |
| Reconfigure per subentry | `config_entries.py:3885`, `:3830` |
| Subentry dibuat langsung dari flow awal | `config_entries.py:3434` |
| Satu device per subentry | `helpers/device_registry.py:1744` |
| Entity menempel ke subentry | `helpers/entity_platform.py:749` |
| `EntitySelector.exclude_entities` + `filter` | `helpers/selector.py:1004` |

Konsekuensi: `min_ha_version` di manifest diset **2026.8.0**. Versi HA yang
lebih lama tidak didukung.

Alternatif yang ditolak: satu entry per objek (tidak ada referential integrity
antar config entry - Billing Group bisa menunjuk source yang sudah dihapus).
