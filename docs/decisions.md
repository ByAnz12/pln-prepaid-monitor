# Catatan keputusan implementasi

Dokumen ini mencatat keputusan yang diambil **saat implementasi**, di luar apa
yang sudah tertulis di [`spec.md`](spec.md) - termasuk satu koreksi terhadap
spec itu sendiri. Urut dari yang terbaru.

Label kepercayaan mengikuti konvensi yang sama dengan `spec.md`
(VERIFIED / LIKELY / UNKNOWN / ASUMSI DESAIN).

---

## D-020 · `token_status` ditunda ke Milestone 5

**Tanggal**: 3 September 2026 · **Milestone 4**

Spec D.2 mendaftarkan `sensor.<mu>_token_status` (normal/warning/critical/
very_critical). Tapi aturan penentuannya di spec I.1 bertumpu pada
`estimated_days_remaining`, yang baru ada setelah Prediction Engine (Milestone
5). Membuatnya sekarang berarti mengirim sensor yang perilakunya berubah besar
milestone depan.

Sebagai gantinya, Milestone 4 mengirim `binary_sensor.<mu>_token_ledger_hold`
untuk keadaan yang memang sudah bisa ditentukan sekarang: ledger sedang
dibekukan menunggu keputusan user.

---

## D-019 · State ledger disimpan di `.storage`, bukan di data config entry

**Tanggal**: 3 September 2026 · **Milestone 4**

Riwayat top-up dan titik awal ledger disimpan lewat `helpers.storage.Store`,
di tempat yang sama dengan state akumulator - bukan di dalam data subentry.

Alasannya bukan selera: integrasi ini memasang *update listener* yang memuat
ulang seluruh entry setiap kali config entry berubah (itu yang membuat
penambahan sumber langsung berlaku tanpa restart). Kalau setiap pencatatan
top-up menulis ke data subentry, setiap top-up akan **me-restart seluruh
integrasi**. Menyimpan ke `.storage` menghindari itu sepenuhnya.

Konfigurasi (aktif/tidak, ambang penahanan) tetap di subentry, karena memang
diubah lewat form dan memang seharusnya memicu muat ulang.

---

## D-018 · Pencatatan token jadi langkah di alur kelompok tagihan, bukan objek tersendiri

**Tanggal**: 3 September 2026 · **Milestone 4**

Spec D.1 mendefinisikan `TokenAccount` sebagai objek dengan id sendiri. Yang
diimplementasikan: **satu langkah di alur kelompok tagihan**.

Alasannya sejalan dengan [D-011](#d-011--billing-group-menerima-beberapa-sumber-langsung-objek-aggregate-ditunda)
dan [D-014](#d-014--tarif-dibuat-sebagai-objek-tersendiri-berbeda-dari-keputusan-aggregate):
objek tersendiri hanya berguna kalau bisa dipakai bersama. Tarif memang bisa
(rumah dan toko sering segolongan), tapi **token tidak pernah** - tiap meteran
prabayar punya salda sendiri, hubungannya selalu satu-ke-satu dengan kelompok
tagihan. Memisahkannya hanya menambah satu konsep tanpa satu pun manfaat.

---

## D-017 · Nilai sisa token dalam Rupiah sengaja tanpa `state_class`

**Tanggal**: 3 September 2026 · **Milestone 4**

`sensor.<mu>_token_remaining_value` memakai `device_class: monetary` **tanpa**
`state_class` sama sekali.

Spec D.2 memberinya `measurement`, tapi `monetary` di Core 2026.8.3 hanya
menerima `total` (lihat [D-012](#d-012--monetary-hanya-menerima-state_class-total-koreksi-spec-d2)),
dan `total` salah artinya untuk angka yang **berkurang**: statistiknya akan
menjumlahkan penurunan sebagai angka negatif.

Tanpa `state_class`, Home Assistant tidak membuat statistik jangka panjang untuk
sensor ini - dan memang tidak seharusnya: "nilai sisa saat ini" bukan besaran
yang masuk akal dijumlahkan sepanjang waktu. Angkanya tetap tampil normal di
dashboard.

---

## D-016 · Sisa token memakai `energy_storage`, bukan `energy` (koreksi spec D.2)

**Tanggal**: 3 September 2026 · **Milestone 4** · **Status: VERIFIED**

Spec D.2 memberi `token_remaining_kwh` kombinasi `device_class: energy` +
`state_class: measurement`. Diverifikasi ke source Core 2026.8.3
(`components/sensor/const.py`), kombinasi itu tidak sah:

```python
SensorDeviceClass.ENERGY: {SensorStateClass.TOTAL, SensorStateClass.TOTAL_INCREASING},
```

`energy` mengasumsikan angka yang naik, sedangkan sisa token justru **turun**.

Yang dipakai: **`device_class: energy_storage`** dengan `state_class:
measurement`. Ini bukan akal-akalan agar lolos validasi - `energy_storage`
berarti "energi yang sedang tersedia", yang justru persis menggambarkan sisa
token, dan Core memang mengizinkannya dengan `measurement`. Bonusnya, sisa token
jadi punya statistik jangka panjang sehingga bisa digrafikkan.

**Catatan**: `spec.md` Bagian D.2 sebaiknya diperbarui mengikuti ini.

---

## D-015 · Biaya beban disebar per hari, bukan ditagihkan sekaligus di awal siklus

**Tanggal**: 3 September 2026 · **Milestone 3**

Spec F.3 menyebut biaya beban ditambahkan "secara prorata" di sensor bulanan
dan tahunan. Yang diimplementasikan: biaya beban dikonversi jadi **Rupiah per
hari** (bulanan dibagi 365,25/12 = 30,44 hari), lalu diakumulasi sesuai lama
siklus yang sudah berjalan.

Efeknya, `cost_this_month` naik mulus dari hari ke hari, bukan melompat di
tanggal 1 lalu diam. Untuk PLN prabayar rumah tangga field ini default **0**
dan tidak berpengaruh sama sekali; ia disediakan untuk golongan bisnis atau
kasus tidak biasa.

Sesuai spec, biaya beban **hanya** masuk ke penghitung bulanan dan tahunan.
Penghitung jam, hari, dan minggu tetap murni berisi biaya energi. Atribut
`energy_cost_only_rp` dan `fixed_charge_included_rp` memisahkan keduanya di
setiap sensor biaya, supaya angkanya selalu jelas asalnya.

---

## D-014 · Tarif dibuat sebagai objek tersendiri (berbeda dari keputusan Aggregate)

**Tanggal**: 3 September 2026 · **Milestone 3**

Di [D-011](#d-011--billing-group-menerima-beberapa-sumber-langsung-objek-aggregate-ditunda)
saya menunda objek Aggregate karena nilai tambahnya tidak terasa. Untuk
**tarif**, keputusannya kebalikan: tarif dibuat sebagai subentry tersendiri yang
bisa dirujuk beberapa kelompok tagihan.

Alasan perbedaannya konkret:

- Rumah dan toko user hampir pasti memakai golongan tarif yang sama. Kalau PLN
  menaikkan tarif, dengan objek bersama cukup diubah **satu kali**; kalau tarif
  menempel di masing-masing kelompok, user harus ingat mengubahnya di dua tempat
  dan angka keduanya bisa diam-diam berbeda.
- Tarif punya **riwayat versi** (spec K.7) yang perlu tempat tinggal sendiri.
- Berbeda dari Aggregate, tarif adalah sesuatu yang user memang sudah
  pikirkan sebagai benda tersendiri ("tarif R-1 saya"), bukan konsep buatan.

Kelompok tagihan boleh **tidak** punya tarif: ia tetap menghitung kWh, hanya
belum punya sensor biaya. Langkah pilih-tarif dilewati otomatis kalau belum ada
tarif sama sekali.

---

## D-013 · Tarif waktu (TOU) dan komponen biaya tambahan ditunda

**Tanggal**: 3 September 2026 · **Milestone 3** · **Sesuai urutan spec L.3**

Spec D.1 mendefinisikan `rate_periods` (beberapa tarif menurut jam) dan
`additional_components` (daftar pajak/komponen lain). Spec L.3 sendiri meminta
Cost Engine dikerjakan "dengan TariffProfile sederhana (flat rate) dulu, baru
tambahkan TOU/additional components" - jadi ini penundaan yang direncanakan,
bukan kelalaian.

Dua alasan tambahan:

- PLN prabayar rumah tangga tidak memakai skema tarif per waktu. Spec sendiri
  menandainya sebagai persiapan "jika suatu saat" dipakai.
- PPJ dan biaya admin - dua komponen yang benar-benar nyata untuk token PLN -
  bukan urusan Cost Engine, melainkan **Token Engine** (spec B.2 dan G).
  Keduanya dipotong dari nominal rupiah saat beli token, bukan ditambahkan ke
  biaya pemakaian.
- Keduanya berbentuk **daftar** yang perlu form berulang; desain UI-nya pantas
  dapat perhatian tersendiri, bukan disisipkan tergesa-gesa.

Model datanya sudah menyediakan tempat, jadi menambahkannya nanti tidak butuh
migrasi.

---

## D-012 · `monetary` hanya menerima `state_class: total` (koreksi spec D.2)

**Tanggal**: 3 September 2026 · **Milestone 3** · **Status: VERIFIED**

Spec bertentangan dengan dirinya sendiri: B.3 menyatakan `monetary` **tidak**
valid dengan `total_increasing`, sedangkan D.2 justru memakainya untuk
`cost_total`.

Diverifikasi langsung ke source Core 2026.8.3,
`homeassistant/components/sensor/const.py`:

```python
SensorDeviceClass.MONETARY: {SensorStateClass.TOTAL},
```

Hanya `total` - bahkan `measurement` pun tidak. Kombinasi yang salah tidak
membuat integrasi gagal, tapi memicu peringatan berulang di log
(`components/sensor/__init__.py`, "is using state class ... which is not
supported").

Yang diimplementasikan: seluruh sensor biaya memakai `state_class: total`.
`cost_total` tanpa `last_reset` (nilainya memang tidak pernah di-reset), dan
penghitung biaya per periode dengan `last_reset` di awal siklusnya - sama
seperti penghitung energi per periode.

**Catatan**: `spec.md` Bagian D.2 sebaiknya diperbarui mengikuti ini.

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

## D-007 · Pengaman ledger token saat reset besar

**Tanggal**: 3 September 2026 · **Disetujui user** · **Terpasang di Milestone 4**

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
