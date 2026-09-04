# PLN Prepaid Energy & Cost Monitor

Integrasi Home Assistant untuk memantau pemakaian listrik prabayar (token) PLN:
membaca meteran pintar yang sudah Anda punya, menghitung biayanya, memperkirakan
kapan token habis, dan mengingatkan Anda sebelum listrik padam.

> **Integrasi ini hanya membaca.**
> Ia tidak pernah bisa memutus atau menyalakan listrik Anda, walaupun MCB pintar
> Anda punya kemampuan relay. Tidak ada tombol, tidak ada saklar, tidak ada
> pemanggilan perintah ke perangkat mana pun. Ini aturan yang dikunci oleh test
> otomatis ([`tests/test_readonly_guarantee.py`](tests/test_readonly_guarantee.py)),
> bukan sekadar janji di dokumentasi.

---

## Daftar isi

1. [Apa yang sudah bisa dipakai sekarang](#apa-yang-sudah-bisa-dipakai-sekarang)
2. [Yang Anda butuhkan](#yang-anda-butuhkan)
3. [Cara memasang](#cara-memasang)
4. [Menambahkan sumber energi pertama](#menambahkan-sumber-energi-pertama)
5. [Entity yang Anda dapatkan](#entity-yang-anda-dapatkan)
6. [Membuat kelompok tagihan](#membuat-kelompok-tagihan)
7. [Mengatur tarif dan menghitung biaya](#mengatur-tarif-dan-menghitung-biaya)
8. [Mencatat token PLN](#mencatat-token-pln)
9. [Memperkirakan kapan token habis](#memperkirakan-kapan-token-habis)
10. [Notifikasi token](#notifikasi-token)
11. [Dashboard](#dashboard)
12. [Kenapa angkanya beda dengan aplikasi meteran?](#kenapa-angkanya-beda-dengan-aplikasi-meteran)
13. [Menambah sumber kedua, ketiga, dst](#menambah-sumber-kedua-ketiga-dst)
14. [Troubleshooting](#troubleshooting)
15. [Untuk pengembang](#untuk-pengembang)
16. [Rencana pengembangan](#rencana-pengembangan)

---

## Apa yang sudah bisa dipakai sekarang

Integrasi ini dibangun bertahap. Yang **sudah selesai dan bisa dipakai**:

- **Pembacaan & penyeragaman sumber energi.** Anda memetakan sensor milik
  meteran/colokan pintar Anda, lalu integrasi ini menghasilkan sensor baru yang
  satuannya seragam (kWh, W, V, A, Hz) dan angkanya aman dari reset counter.
- **Deteksi sumber putus** dengan masa tenggang yang bisa Anda atur.
- **Kelompok tagihan** yang menggabungkan satu atau beberapa meteran, lengkap
  dengan penghitung pemakaian **jam ini / hari ini / minggu ini / bulan ini /
  tahun ini** yang di-reset otomatis tiap siklus.
- **Riwayat jangka panjang** otomatis lewat long-term statistics Home Assistant,
  siap dipakai kartu grafik bawaan dan Energy Dashboard.
- **Perhitungan biaya dalam Rupiah**, dengan tarif yang bisa dipakai bersama
  beberapa kelompok tagihan dan riwayat perubahannya tersimpan.
- **Pencatatan sisa token PLN** yang berdiri sendiri: Anda catat tiap
  pengisian, sistem mengurangi sesuai pemakaian nyata, lengkap dengan koreksi
  salah input dan pengaman saat meteran ter-reset.
- **Perkiraan kapan token habis** dari pemakaian Anda sendiri, lengkap dengan
  tingkat keyakinan - dan diam saja selama datanya belum cukup.
- **Notifikasi bertingkat** ke Telegram dan/atau Home Assistant, dikirim hanya
  saat status berpindah tingkat, menghormati jam tenang.
- **Dashboard yang dibuatkan otomatis**, dengan entity_id yang sudah benar dan
  hanya memakai kartu bawaan Home Assistant.

Yang **belum** (lihat [Rencana pengembangan](#rencana-pengembangan)): pembersihan
data lama.

---

## Yang Anda butuhkan

- Home Assistant **2026.8.0 atau lebih baru** (integrasi ini memakai fitur
  *config subentry* yang belum ada di versi lama).
- Minimal satu perangkat yang sudah muncul di Home Assistant dan melaporkan
  **pemakaian listrik**, misalnya MCB pintar, colokan pintar, atau inverter.
  Protokolnya tidak penting sama sekali - Zigbee, Tuya/WiFi, Modbus, atau ESPHome
  semuanya diperlakukan sama.
- Untuk setiap perangkat itu, minimal salah satu dari:
  - sensor **energi (kWh)** yang angkanya terus bertambah, **atau**
  - sensor **daya (W)** yang menunjukkan pemakaian saat ini.

Anda **tidak** perlu akun PLN, API, atau koneksi internet apa pun.

---

## Cara memasang

### Pilihan A - lewat HACS (disarankan)

1. Buka **HACS** di Home Assistant.
2. Klik menu titik tiga di kanan atas -> **Custom repositories**.
3. Tempelkan URL repositori ini, pilih kategori **Integration**, lalu **Add**.
4. Cari "PLN Prepaid Energy & Cost Monitor", klik **Download**.
5. **Restart Home Assistant** (Settings -> System -> tombol daya kanan atas ->
   Restart Home Assistant).

### Pilihan B - salin manual

1. Salin folder `custom_components/pln_prepaid_monitor` ke folder konfigurasi
   Home Assistant Anda, sehingga jadi:
   `/config/custom_components/pln_prepaid_monitor/`
2. **Restart Home Assistant.**

> Kalau folder `custom_components` belum ada di `/config`, buat dulu folder itu.

---

## Menambahkan sumber energi pertama

Setelah restart:

1. Buka **Settings -> Devices & Services**.
2. Klik **+ Add Integration** di kanan bawah.
3. Ketik **PLN Prepaid** dan pilih integrasinya.

Anda akan melewati tiga langkah.

### Langkah 1 - Pilih perangkat (boleh dilewati)

Daftar ini hanya berisi perangkat yang punya sensor energi atau daya. Kalau Anda
memilih salah satu, isian di langkah berikutnya akan **ditebak otomatis** dari
sensor milik perangkat itu.

Kalau perangkat Anda tidak muncul, kosongkan saja dan pilih sensornya satu per
satu di langkah berikutnya. Kalau nanti sensornya juga tidak muncul di sana,
centang **"Tampilkan semua sensor tanpa penyaringan"**.

### Langkah 2 - Petakan sensor

Di sini Anda memberi tahu sistem sensor mana mewakili apa. Contoh isian nyata
untuk sebuah MCB rumah:

| Isian | Contoh |
|---|---|
| Nama sumber energi | `MCB RUMAH` |
| Sensor energi (kWh) | `sensor.mcb_rumah_total_energy` |
| Sensor daya (W) | `sensor.mcb_rumah_phase_a_power` |
| Sensor tegangan (V) | `sensor.mcb_rumah_phase_a_voltage` |
| Sensor arus (A) | `sensor.mcb_rumah_phase_a_current` |
| Sensor frekuensi (Hz) | `sensor.mcb_rumah_supply_frequency` |
| Masa tenggang | `5` menit |

Yang wajib hanya **nama** dan **salah satu** dari energi atau daya. Sisanya
opsional.

**Mana yang lebih penting, kWh atau W?** kWh. Anggap begini: sensor kWh itu
seperti **angka odometer di motor** - terus bertambah, tidak pernah mundur.
Sensor daya (W) itu seperti **jarum speedometer** - menunjukkan keadaan detik
ini saja. Untuk menghitung pemakaian dan biaya, yang dipakai adalah odometer.
Kalau Anda hanya punya speedometer, sistem masih bisa memperkirakan jarak
tempuhnya, tapi hasilnya perkiraan.

> Anda tidak akan menemukan tombol, relay, atau pengaturan perangkat di daftar
> pilihan mana pun di sini. Itu disengaja: hanya sensor pembacaan yang
> ditampilkan, sehingga saklar MCB Anda mustahil terpilih karena salah klik.

### Langkah 3 - Cek dulu sebelum disimpan

Halaman ini menunjukkan apa yang **sebenarnya dibaca** sistem dari sensor yang
Anda pilih. Bacalah sebentar - terutama baris bertanda ⚠️. Contoh yang sering
muncul dan artinya:

| Yang muncul | Artinya |
|---|---|
| ⚠️ Satuan sensor ini kW, dikonversi otomatis ke W | Aman. Sebagian meteran melaporkan kW, sebagian W. Sistem menyeragamkannya. |
| ⚠️ Sensor ini tidak memberi tahu Home Assistant bahwa angkanya selalu naik | Aman, tapi periksa lagi. Sistem akan memperlakukannya sebagai penghitung naik. |
| ⚠️ Sensor sedang tidak tersedia | Aman. Boleh disimpan; sistem menunggu sampai sensornya hidup. |
| ⚠️ Tidak ada sensor kWh yang dipilih | Perhatikan. Angka kWh akan jadi perkiraan, bukan pembacaan asli. |
| ⛔ (tanda merah) | Harus diperbaiki dulu, tombol simpan tidak akan melanjutkan. |

Klik **Submit** untuk menyimpan. Selesai.

---

## Entity yang Anda dapatkan

Untuk setiap sumber energi, Anda mendapat satu **perangkat baru** di Home
Assistant dengan entity berikut (contoh untuk sumber bernama "MCB RUMAH"):

| Entity | Isinya | Kegunaan |
|---|---|---|
| `sensor.mcb_rumah_energy` | Total kWh, selalu naik | **Yang terpenting.** Jadi dasar semua perhitungan biaya dan token nanti. Juga bisa langsung dipakai di Energy Dashboard bawaan Home Assistant. |
| `sensor.mcb_rumah_power` | Daya sekarang, dalam Watt | Untuk melihat beban langsung. Selalu Watt, walaupun meteran aslinya melaporkan kW. |
| `sensor.mcb_rumah_voltage` | Tegangan, dalam Volt | Sekadar informasi. |
| `sensor.mcb_rumah_current` | Arus, dalam Ampere | Sekadar informasi. |
| `sensor.mcb_rumah_frequency` | Frekuensi, dalam Hertz | Sekadar informasi. |
| `binary_sensor.mcb_rumah_connection_status` | Terhubung / tidak | Menyala "off" hanya kalau sumber hilang **lebih lama** dari masa tenggang, jadi tidak berisik untuk gangguan sedetik-dua detik. |

Setiap sensor membawa keterangan tambahan (atribut) yang bisa Anda lihat lewat
**Developer Tools -> States**, antara lain:

- `source_entity_id` - sensor asli yang dibaca
- `source_raw_value` dan `source_unit` - angka & satuan aslinya sebelum dikonversi
- `unit_conversion_factor` - pengalinya (misal 1000 untuk kW -> W)
- `source_of_truth` - `cumulative` (dari sensor kWh asli) atau
  `integrated_from_power` (perkiraan dari daya)
- `resets_detected` - berapa kali counter meteran terdeteksi ter-reset
- `last_reset_detected_at`, `last_reset_from_kwh`, `last_reset_to_kwh` - kapan
  reset terakhir terjadi dan dari angka berapa ke angka berapa
- `holding_last_value` - `true` saat sistem sedang menahan nilai lama karena
  sumbernya hilang sebentar

### Kalau Anda curiga meteran Anda ter-reset sendiri

Buka **Developer Tools -> States**, cari entity `..._energy` milik sumber itu,
lalu lihat atribut `resets_detected`. Kalau angkanya bertambah dari waktu ke
waktu padahal Anda tidak mengganti apa pun, meteran itu memang ter-reset
sendiri - dan `last_reset_detected_at` beserta `last_reset_from_kwh` /
`last_reset_to_kwh` memberi tahu Anda kapan dan sebesar apa.

Ini berguna untuk membedakan dua hal yang mudah tertukar: **penghitung token
bawaan perangkat** yang ter-reset (tidak mempengaruhi sistem ini sama sekali),
versus **sensor energi kumulatif** yang ikut ter-reset (ditangani otomatis oleh
sistem ini, dan tercatat di sini).

---

## Membuat kelompok tagihan

Sumber energi hanya membaca meteran. Untuk mendapat penghitung **pemakaian hari
ini / bulan ini** dan (nanti) perhitungan biaya serta token, Anda perlu membuat
**kelompok tagihan**.

Kelompok tagihan = satu "tagihan listrik". Contoh susunan yang umum untuk dua
meteran terpisah:

| Kelompok tagihan | Isinya |
|---|---|
| PLN Rumah | sumber "MCB Rumah" |
| PLN Toko | sumber "MCB Toko" |

Keduanya dihitung sendiri-sendiri dan nanti punya token sendiri-sendiri.

Kalau satu tagihan listrik Anda diukur oleh **beberapa meteran sekaligus**,
masukkan semuanya ke satu kelompok - pemakaiannya akan dijumlahkan.

### Langkahnya

1. Buka **Settings → Devices & Services → PLN Prepaid Energy & Cost Monitor**.
2. Klik **Tambah kelompok tagihan**.
3. **Langkah 1** - beri nama, lalu centang sumber energi yang termasuk.
4. **Langkah 2** - pilih penghitung mana saja yang ingin Anda lihat, dan kapan
   tiap penghitung dimulai lagi dari nol:

   | Pengaturan | Bawaan | Ubah kalau... |
   |---|---|---|
   | Penghitung yang dibuat | jam, hari, minggu, bulan, tahun | Anda tidak butuh semuanya dan ingin daftar entity lebih ringkas |
   | Jam mulai hari baru | 00:00 | Anda ingin "satu hari" dihitung mulai jam lain, misalnya jam buka toko |
   | Hari mulai minggu baru | Senin | Anda menghitung minggu mulai hari lain |
   | Tanggal mulai bulan baru | 1 | Anda ingin siklus bulanan mengikuti tanggal lain (maksimal 28) |
   | Bulan mulai tahun baru | Januari | Anda ingin tahun buku, bukan tahun kalender |

5. **Langkah 3** - halaman ringkasan menunjukkan sumber apa saja yang digabung
   dan kapan tiap penghitung akan di-reset berikutnya. Kalau ada sumber yang
   sudah dipakai kelompok lain, peringatannya muncul di sini.

### Entity yang dihasilkan kelompok tagihan

Untuk kelompok bernama "PLN Rumah":

| Entity | Isinya |
|---|---|
| `sensor.pln_rumah_energy_total` | Total kWh gabungan seluruh anggota, selalu naik |
| `sensor.pln_rumah_power` | Daya gabungan saat ini, dalam Watt |
| `sensor.pln_rumah_energy_this_hour` | Pemakaian jam ini |
| `sensor.pln_rumah_energy_this_day` | Pemakaian hari ini |
| `sensor.pln_rumah_energy_this_week` | Pemakaian minggu ini |
| `sensor.pln_rumah_energy_this_month` | Pemakaian bulan ini |
| `sensor.pln_rumah_energy_this_year` | Pemakaian tahun ini |

Tiap penghitung periode membawa atribut `cycle_start` (kapan siklus ini dimulai)
dan `next_cycle_start` (kapan akan di-reset), plus `member_sources` dan
`members_unavailable` supaya Anda tahu kalau salah satu meteran sedang mati -
karena selama meteran itu mati, pemakaiannya memang tidak terhitung.

> **Kenapa tidak ada sensor tegangan/arus gabungan?** Karena menjumlahkan
> tegangan dua meteran di rangkaian berbeda tidak berarti apa-apa secara fisika.
> Daya dan energi memang sah dijumlahkan; tegangan tidak. Kalau kelompok Anda
> hanya berisi satu meteran, pakai saja sensor tegangan milik sumbernya.

### Riwayat dan Energy Dashboard

`sensor.<kelompok>_energy_total` sudah punya penanda yang benar untuk masuk ke
**long-term statistics** Home Assistant, yang tidak pernah dihapus otomatis.
Artinya Anda bisa langsung:

- menambahkan kartu **Statistics graph** untuk melihat grafik harian/bulanan;
- mendaftarkannya di **Settings → Dashboards → Energy** sebagai sumber
  konsumsi grid, kalau Anda ingin memakai Energy Dashboard bawaan.

---

## Mengatur tarif dan menghitung biaya

Kelompok tagihan menghitung kWh. Untuk mengubahnya jadi Rupiah, Anda perlu
membuat **tarif**.

Tarif sengaja dibuat sebagai benda tersendiri, bukan menempel di kelompok
tagihan. Alasannya praktis: rumah dan toko Anda kemungkinan besar memakai
golongan tarif yang sama, jadi kalau PLN menaikkan tarif Anda cukup mengubahnya
**sekali** dan kedua kelompok ikut menyesuaikan.

### Membuat tarif

1. Buka **Settings → Devices & Services → PLN Prepaid Energy & Cost Monitor**.
2. Klik **Tambah tarif**.

| Isian | Bawaan | Penjelasan |
|---|---|---|
| Nama tarif | — | Misalnya `R-1 1300VA` atau `Tarif Toko` |
| Tarif per kWh (Rp) | `1444,70` | **Hanya perkiraan** — lihat peringatan di bawah |
| Biaya beban (Rp) | `0` | Untuk PLN prabayar rumah tangga biasanya 0 |
| Biaya beban ditagih per | Bulan | Tidak berpengaruh kalau biaya bebannya 0 |
| Cara pembulatan | Ke yang terdekat | Hanya mempengaruhi tampilan |
| Bulatkan ke kelipatan (Rp) | `1` | Isi 0 kalau tidak ingin dibulatkan |

> ### ⚠️ Angka Rp 1.444,70 itu hanya perkiraan
>
> Itu perkiraan untuk golongan **R-1 daya 1300–2200 VA**, dikumpulkan dari
> pemberitaan tarif — **bukan** angka resmi dari PLN, dan tarif berubah dari
> waktu ke waktu serta berbeda antar golongan daya dan wilayah.
>
> **Anda wajib menggantinya dengan tarif Anda sendiri.** Cara melihatnya:
> struk atau bukti pembelian token terakhir, aplikasi PLN Mobile, atau tagihan
> resmi PLN. Perkiraan kasarnya: bagi nominal rupiah yang benar-benar masuk
> sebagai kWh dengan jumlah kWh yang Anda terima.

### Menghubungkan tarif ke kelompok tagihan

Saat membuat atau mengubah kelompok tagihan, kini ada satu langkah tambahan
untuk memilih tarif. Langkah itu **dilewati otomatis** kalau Anda belum punya
tarif sama sekali — kelompoknya tetap bisa dibuat, hanya belum menghitung biaya.

### Entity biaya yang dihasilkan

| Entity | Isinya |
|---|---|
| `sensor.pln_rumah_cost_total` | Total biaya sejak pemantauan dimulai |
| `sensor.pln_rumah_cost_this_day` | Biaya hari ini |
| `sensor.pln_rumah_cost_this_month` | Biaya bulan ini |
| … | satu sensor untuk tiap periode yang Anda aktifkan |

Setiap sensor biaya membawa atribut `energy_cost_only_rp` (biaya murni dari
listrik yang dipakai) dan `fixed_charge_included_rp` (bagian biaya beban),
supaya angkanya selalu jelas asalnya. Sensor total juga membawa `rate_history`,
yaitu riwayat semua perubahan tarif berikut tanggalnya.

### Kalau tarif PLN naik

Ubah saja tarifnya lewat **Ubah tarif**. Dua hal yang terjadi:

1. Versi tarif lama **tetap disimpan** sebagai riwayat, tidak ditimpa.
2. Biaya yang sudah tercatat **tidak dihitung ulang**. Ini memang benar:
   listrik yang Anda pakai bulan lalu memang dipakai pada tarif lama. Tarif baru
   berlaku untuk pemakaian mulai saat itu.

### Catatan penting soal arti angkanya

Sensor biaya menjawab pertanyaan *"listrik yang saya pakai ini setara berapa
rupiah?"* — dihitung dari tarif dasar per kWh.

Itu **bukan** jumlah uang yang perlu Anda bayar untuk membeli kWh sebanyak itu.
Saat membeli token, nominal rupiah Anda dipotong dulu oleh biaya admin dan PPJ
sebelum sisanya dibagi tarif. Jadi untuk mendapat 10 kWh, uang yang keluar
selalu lebih besar daripada angka di sensor ini. Perhitungan token akan
ditangani terpisah di tahap berikutnya.

---

## Mencatat token PLN

Tidak ada layanan resmi PLN yang bisa dibaca untuk mengetahui sisa token Anda.
Jadi sistem ini memakai cara yang tidak bergantung pada siapa pun:

1. Setiap kali Anda mengisi token, Anda catat **berapa kWh yang masuk**.
2. Sistem mengurangi angka itu sesuai **pemakaian nyata** yang terbaca dari
   meteran Anda sendiri.

Hasilnya adalah catatan sisa token yang **berdiri sendiri** — tetap benar
walaupun penghitung token bawaan meteran Anda bermasalah.

### Mengaktifkan

Saat membuat atau mengubah kelompok tagihan, ada langkah **Pencatatan token
PLN**. Centang untuk mengaktifkan. Di situ juga ada satu pengaman yang
dijelaskan di bawah.

Setelah aktif, kelompok itu mendapat entity berikut:

| Entity | Isinya |
|---|---|
| `sensor.pln_rumah_token_remaining` | Sisa token dalam kWh |
| `sensor.pln_rumah_token_consumed` | Sudah terpakai berapa kWh dari token |
| `sensor.pln_rumah_token_remaining_value` | Perkiraan nilai sisa dalam Rupiah |
| `binary_sensor.pln_rumah_token_ledger_hold` | Menyala kalau perhitungan sedang dibekukan |

### Mencatat pengisian token

Buka **Developer Tools → Actions**, cari **Catat pengisian token**, pilih
perangkat kelompok tagihannya, lalu isi:

- **kWh yang masuk** — angka kWh yang benar-benar masuk ke meteran, seperti di
  struk atau di layar meteran sesudah token dimasukkan. **Bukan** nominal
  rupiahnya.
- **Nominal pembelian** (opsional) — hanya untuk catatan Anda.
- Angka meteran sebelum/sesudah (opsional) — untuk pencocokan.

Pengisian bersifat **menambah**. Kalau sisa Anda masih 30 kWh lalu Anda isi 40
kWh, sisanya jadi 70 kWh — bukan 40. Ini sudah dicocokkan dengan perilaku meteran
fisik.

### Nilai pengisian siap pakai

Kalau Anda selalu membeli token dengan nominal yang sama, daftarkan sekali dan
Anda tidak perlu mengetik ulang angka kWh setiap kali.

Buka **Ubah kelompok tagihan → Pencatatan token PLN**, lalu isi kotak **Nilai
pengisian siap pakai**, satu baris satu nilai:

```
1.000.000 = 826,50
500.000 = 413,25
```

Angka kWh diambil dari struk pembelian, bagian **Jumlah Kwh**.

> ### ⚠️ Perhatikan satuan di struk
>
> Struk PLN menulis jumlah kWh dalam satuan **0,01 kWh**. Struk yang tertulis
> **82650 KWM** berarti **826,50 kWh** di layar meteran — bagi 100.
>
> Kalau Anda terlanjur memasukkan 82650, sistem akan menolaknya dan menyarankan
> angka yang benar. Batas kewajarannya 20.000 kWh sekali isi, jadi pembelian
> sebesar apa pun yang wajar tetap lolos.

Setelah didaftarkan, ada **tiga cara** mencatat pengisian:

| Cara | Kapan dipakai |
|---|---|
| **Tombol di dashboard** | Pembelian rutin — sekali klik, ada konfirmasi |
| **Sebut nominalnya saja** | Lewat Developer Tools: isi *Nominal pembelian* `1000000`, biarkan kWh kosong |
| **Ketik kWh manual** | Pembelian dengan nominal tidak biasa |

Kalau Anda mengisi keduanya, angka kWh yang Anda ketik yang dipakai — isian
manual selalu menang atas preset.

Nominal yang belum terdaftar akan ditolak dengan pesan jelas, bukan diam-diam
mencatat nol kWh.

### Kalau salah ketik

Semua bisa dikoreksi, dan sisa token langsung dihitung ulang dari seluruh
riwayat:

| Kalau… | Pakai layanan |
|---|---|
| Angka kWh salah ketik | **Perbaiki catatan pengisian** |
| Satu pengisian tercatat dua kali | **Hapus catatan pengisian** |
| Angka sistem melenceng dari layar meteran | **Samakan dengan angka meteran** |
| Meteran fisik diganti | **Mulai pencatatan token dari nol** |

Kode entri (`topup_id`) yang dibutuhkan untuk memperbaiki atau menghapus bisa
dilihat di atribut `topup_history` pada sensor sisa token, lewat
**Developer Tools → States**.

### Pengaman saat meteran ter-reset

Ini bagian yang dirancang khusus untuk masalah yang Anda alami.

Kalau meteran ter-reset dan angka pertama sesudahnya **besar** — misalnya karena
meteran diganti dan meteran barunya sudah menunjukkan ribuan kWh — angka itu akan
terbaca sebagai "pemakaian baru" yang sangat besar. Kalau dibiarkan, sisa token
Anda bisa langsung terbaca habis padahal sebenarnya masih banyak.

Karena itu sistem **berhenti dan bertanya** alih-alih menebak:

1. `binary_sensor.<kelompok>_token_ledger_hold` menyala.
2. Sisa token **dibekukan** di angka terakhir sebelum kejadian — tidak ikut
   hangus.
3. Atributnya menunjukkan dari sumber mana, kapan, dan dari angka berapa ke
   angka berapa.

Anda lalu memutuskan lewat layanan **Putuskan penahanan token**:

| Pilihan | Kapan dipakai |
|---|---|
| **Anggap pemakaian nyata** | Listriknya memang terpakai sebanyak itu |
| **Abaikan** | Meteran diganti, atau angkanya kacau bukan karena pemakaian |
| **Kalibrasi dari angka meteran** | Anda ingin memasukkan sisa kWh yang tertera di layar |

Reset firmware biasa — yang jatuh ke hampir nol — **tidak** memicu ini, jadi Anda
tidak akan diganggu untuk hal sepele. Ambangnya bisa Anda atur di langkah
Pencatatan token PLN (bawaan: 1 kWh).

### Catatan penting soal nilai Rupiah-nya

`token_remaining_value` = sisa kWh × tarif. Itu perkiraan **nilai** sisa token
Anda.

Itu **bukan** jumlah uang untuk membeli kWh sebanyak itu. Saat beli token,
nominal Anda dipotong biaya admin dan PPJ dulu, jadi uang yang keluar selalu
lebih besar.

---

## Memperkirakan kapan token habis

Setelah beberapa hari berjalan, sistem mulai bisa memperkirakan kapan token Anda
habis - dihitung dari **pemakaian Anda sendiri** yang terekam di riwayat Home
Assistant, bukan dari angka umum.

| Entity | Isinya |
|---|---|
| `sensor.pln_rumah_average_daily_usage` | Rata-rata pemakaian per hari |
| `sensor.pln_rumah_estimated_days_remaining` | Perkiraan berapa hari lagi habis |
| `sensor.pln_rumah_estimated_empty_date` | Perkiraan tanggalnya |
| `sensor.pln_rumah_token_status` | Aman / perlu perhatian / kritis / sangat kritis |
| `binary_sensor.pln_rumah_data_sufficient` | Apakah datanya sudah cukup |

### Selama data belum cukup, tidak ada angka sama sekali

Ini disengaja. Sensor perkiraan akan **kosong** (`unavailable`) sampai riwayat
pemakaian Anda memadai, dan `binary_sensor.<kelompok>_data_sufficient` mati.
Lebih baik jujur belum tahu daripada memberi tanggal yang terdengar pasti
padahal ditebak dari dua hari pemakaian.

Baru dipasang? Tunggu beberapa hari. Kalau ingin lebih cepat, sistem otomatis
turun ke rentang **24 jam** begitu ada 6 jam data — dengan tingkat keyakinan
yang diturunkan, dan itu tercantum di atribut `confidence`.

### Bagaimana angkanya dihitung

1. **Rata-rata harian** diambil dari rentang pilihan Anda (bawaan 7 hari).
2. **Peredam anomali** (bawaan: median) menahan satu hari luar biasa — tamu
   menginap, AC seharian — agar tidak menggeser perkiraan sebulan ke depan.
3. **Margin aman** (bawaan 10%) membuat perkiraan sedikit pesimistis, supaya
   Anda mengisi token sedikit lebih awal, bukan sedikit terlambat.
4. **Hari tersisa** = sisa kWh ÷ rata-rata yang sudah diberi margin.

Setiap sensor perkiraan membawa atribut `window_used`, `data_points`, dan
`confidence`, jadi Anda selalu bisa melihat angka itu berdasarkan apa.

| `confidence` | Artinya |
|---|---|
| `high` | 7 hari penuh data, atau 30 hari penuh |
| `medium` | Rentang 24 jam, atau 3–6 hari data |
| `low` | Rentang 30 hari yang belum penuh |
| `insufficient_data` | Belum cukup data — sensor kosong |
| `insufficient_usage` | Pemakaian nyaris nol, hari tersisa tidak bisa dihitung |

### Status token

| Status | Kapan |
|---|---|
| **Aman** | Di atas ambang peringatan |
| **Perlu perhatian** | Tersisa ≤ 7 hari (bisa diatur) |
| **Kritis** | Tersisa ≤ 3 hari (bisa diatur) |
| **Sangat kritis** | Tersisa ≤ 1 hari, **atau** sisa kWh di bawah ambang kWh |
| **Ditahan** | Ledger sedang dibekukan menunggu keputusan Anda |
| **Belum diketahui** | Data belum cukup untuk menyimpulkan |

Ambang kWh adalah jaring pengaman yang **tidak bergantung pada prediksi** —
berguna justru saat perkiraan belum tersedia. Isi 0 untuk mematikannya.

Semua ambang dan cara perhitungan diatur di langkah **Peringatan dan perkiraan**
saat membuat atau mengubah kelompok tagihan. Sistem menolak ambang yang tidak
berurutan (peringatan harus lebih besar dari kritis, kritis lebih besar dari
sangat kritis) — bukan diam-diam membetulkannya.

---

## Notifikasi token

Sistem bisa mengirim peringatan sebelum token habis - ke Telegram, ke notifikasi
Home Assistant, atau keduanya.

### Mengatur

Saat membuat atau mengubah kelompok tagihan, ada langkah **Notifikasi token**.
Kalau integrasi Telegram sudah terpasang di Home Assistant, target Telegram-nya
**muncul otomatis** di daftar pilihan — Anda tidak perlu mengetik nama service.

| Pengaturan | Bawaan | Untuk apa |
|---|---|---|
| Kirim notifikasi token | mati | Nyalakan untuk mulai menerima peringatan |
| Kirim ke | — | Pilih satu atau beberapa tujuan dari daftar |
| Tampilkan juga di Home Assistant | menyala | Cadangan kalau Telegram bermasalah |
| Awalan pesan | `[Token PLN]` | Membedakan dari pesan automation lain |
| Beri tahu saat token terisi lagi | menyala | Satu pesan penutup saat sudah aman |
| Ulangi selama belum diisi | mati | Kalau mati, satu tingkat cukup sekali |
| Jarak minimum antar pengulangan | 12 jam | Hanya berlaku kalau pengulangan menyala |
| Jam tenang | — | Misalnya 22:00–06:00 |
| Sangat kritis boleh menembus jam tenang | menyala | Tetap dibangunkan kalau hampir padam |

> **Awalan `[Token PLN]` itu penting.** Anda sudah punya automation lain yang
> mengirim pesan soal listrik padam/nyala. Awalan ini membuat dua sistem itu
> mudah dibedakan begitu pesannya masuk.

### Kapan pesan dikirim

Pesan hanya dikirim **saat status berpindah tingkat** — misalnya dari aman ke
perlu perhatian, atau dari kritis ke sangat kritis. Selama statusnya belum
berubah, sistem diam. Peringatan yang datang tiap lima menit hanya akan berhenti
dibaca.

| Kejadian | Yang terjadi |
|---|---|
| Aman → perlu perhatian | Satu pesan |
| Perlu perhatian → tetap perlu perhatian | Tidak ada pesan |
| Perlu perhatian → kritis | Satu pesan lagi |
| Anda isi token, status kembali aman | Satu pesan penutup |
| Pencatatan token ditahan | Satu pesan + kartu di menu Perbaikan |

### Jam tenang

Pesan yang tertahan jam tenang **tidak hilang** — ia dikirim begitu jam tenang
lewat. Kecuali status **sangat kritis**, yang boleh menembus kalau Anda
mengizinkannya, karena listrik yang benar-benar hampir padam pantas
membangunkan Anda.

### Kalau pencatatan token ditahan

Selain pesan, muncul juga kartu di **Settings → System → Repairs** yang
menjelaskan meteran mana yang melompat dan ke angka berapa, lengkap dengan tiga
pilihan keputusan Anda. Kartu itu hilang sendiri begitu Anda memutuskan.

---

## Dashboard

Integrasi ini bisa **membuatkan dashboard** untuk Anda, lengkap dengan
`entity_id` yang sudah benar.

### Cara membuatnya

1. Buka **Developer Tools → Actions**.
2. Cari **Buatkan dashboard**, klik **Perform action**.
3. Salin isi `yaml` dari hasilnya.
4. Buka **Settings → Dashboards → + Add dashboard → New dashboard from
   scratch**, beri nama.
5. Buka dashboard barunya → ikon pensil di kanan atas → titik tiga → **Raw
   configuration editor** → tempelkan, lalu simpan.

Ada juga contoh statis di [docs/dashboard-example.yaml](docs/dashboard-example.yaml)
kalau Anda ingin melihat bentuknya lebih dulu — tapi untuk dipakai sungguhan,
pakai hasil dari layanan di atas, karena `entity_id`-nya sudah disesuaikan
dengan nama kelompok tagihan Anda sendiri.

### Isinya

Satu halaman per kelompok tagihan, berisi:

| Bagian | Isinya |
|---|---|
| **Status** | Status token, perkiraan hari tersisa, tanggal habis, kecukupan data |
| **Sekarang** | Gauge daya, tegangan/arus/frekuensi per meteran, status koneksi |
| **Pemakaian** | Penghitung jam ini / hari ini / minggu ini / bulan ini / tahun ini |
| **Biaya** | Penghitung biaya untuk periode yang sama |
| **Token** | Sisa kWh, nilai Rupiah, terpakai, rata-rata harian |
| **Riwayat** | Grafik batang pemakaian dan biaya harian 30 hari terakhir |

Kartu penahanan ledger **hanya muncul saat memang sedang ditahan**, lengkap
dengan dua tombol keputusan yang meminta konfirmasi dulu.

Dashboard menyesuaikan diri: kelompok tanpa tarif tidak mendapat kartu biaya,
kelompok tanpa token tidak mendapat kartu token, dan periode yang tidak Anda
aktifkan tidak ikut muncul.

### Semua kartu bawaan Home Assistant

Tidak ada kartu HACS, Mushroom, atau pihak ketiga mana pun — dashboard ini jalan
di Home Assistant polos. Kalau Anda suka tampilan Mushroom, silakan tambahkan
sendiri di atasnya.

Dua hal yang mungkin ingin Anda sesuaikan setelah menempel:

- **Batas atas gauge daya** diisi 5000 W. Ubah `max:` sesuai daya terpasang
  Anda supaya jarumnya proporsional.
- **Rentang grafik riwayat** diisi 30 hari (`days_to_show`).

### Mencatat pengisian token dari dashboard

Kalau Anda sudah mendaftarkan [nilai pengisian siap
pakai](#nilai-pengisian-siap-pakai), dashboard menampilkan **tombol sekali
klik** untuk tiap nilai — misalnya "Rp 1.000.000 (826,50 kWh)". Tombolnya
meminta konfirmasi dulu sebelum mencatat.

Untuk pengisian dengan angka bebas, kartu **Cara mencatat pengisian token**
menjelaskan langkahnya lewat **Developer Tools → Actions**. Home Assistant tidak
punya kartu bawaan yang bisa menampilkan form berisi angka lalu memanggil
layanan dengan isian itu, jadi angka bebas memang perlu lewat sana.

---

## Kenapa angkanya beda dengan aplikasi meteran?

Sensor `..._energy` buatan integrasi ini **dimulai dari angka yang sama** dengan
meteran Anda saat pertama kali dipasang - angka yang di aplikasi Smart Life
disebut *total forward energy*. Jadi awalnya keduanya cocok.

Keduanya bisa berbeda dalam dua keadaan:

1. **Meteran Anda di-reset atau diganti.** Angka di aplikasi kembali ke nol,
   sedangkan angka kita **terus naik**. Ini disengaja: kalau angka kita ikut
   jatuh, seluruh perhitungan biaya dan sisa token akan kacau. Atribut
   `resets_detected` akan bertambah, dan kejadiannya dicatat di log Home
   Assistant.
2. **Sumber sempat hilang lama.** Pemakaian selama sumber mati memang tidak
   terbaca oleh siapa pun, jadi tidak diisi mundur atau ditebak. Selisihnya akan
   tetap ada, dan itu jujur.

Untuk memeriksa kecocokan kapan saja, bandingkan atribut `source_raw_value`
(angka mentah dari meteran) dengan angka di aplikasi - keduanya harus selalu
sama persis.

---

## Menambah sumber kedua, ketiga, dst

Misalnya Anda punya MCB rumah **dan** MCB toko:

1. Buka **Settings -> Devices & Services -> PLN Prepaid Energy & Cost Monitor**.
2. Klik **Tambah sumber energi**.
3. Ikuti tiga langkah yang sama seperti tadi.

Setiap sumber berdiri sendiri: punya perangkat sendiri, entity sendiri, dan
perhitungan sendiri. Untuk mengubah atau menghapusnya, klik titik tiga di sebelah
nama sumber tersebut.

Nama sumber tidak boleh sama persis dengan sumber yang sudah ada - sistem akan
menolaknya supaya Anda tidak tertukar saat melihat dashboard.

---

## Troubleshooting

### Sensor yang saya cari tidak muncul di daftar pilihan

Kembali ke langkah pertama dan centang **"Tampilkan semua sensor tanpa
penyaringan"**. Penyaringan default hanya menampilkan sensor yang sudah memberi
tahu Home Assistant jenis pengukurannya; sebagian sensor tidak melakukan itu.

Kalau tetap tidak muncul, sensor itu mungkin memang bukan domain `sensor`
(misalnya entity tombol atau saklar) - dan itu memang sengaja tidak pernah
ditampilkan di sini.

### Entity saya `unavailable`

1. Cek dulu sensor aslinya di **Developer Tools -> States**. Kalau sumbernya
   sendiri `unavailable`, masalahnya ada di perangkat/integrasi aslinya, bukan di
   sini.
2. `binary_sensor.<nama>_connection_status` akan bernilai `off` kalau sumber
   sudah hilang lebih lama dari masa tenggang yang Anda atur.
3. Kalau gangguan singkat terlalu sering membuatnya berkedip, perbesar masa
   tenggang lewat **Tambah/ubah sumber energi -> Masa tenggang**.

### Angka energinya tidak bertambah

- Kalau `source_of_truth` bernilai `cumulative`: periksa apakah sensor aslinya
  memang bertambah. Kalau sensor aslinya diam, kita juga diam - itu benar.
- Kalau bernilai `integrated_from_power`: angkanya diperbarui setiap 30 detik
  selama sumbernya sehat. Kalau tetap diam, cek `binary_sensor` status koneksi.

### Angkanya melonjak besar sekali sekaligus

Ini terjadi kalau meteran melaporkan penurunan besar (lebih dari 10%), yang
diperlakukan sebagai reset counter. Aturan ini sengaja dibuat sama persis dengan
aturan bawaan Home Assistant, supaya angka kita tidak pernah bertentangan dengan
statistik bawaannya. Kalau ini terjadi karena Anda **mengganti meteran fisik**,
lonjakan itu wajar - dan nanti akan ada layanan khusus untuk mengatur ulang
titik awal perhitungan (lihat rencana pengembangan).

Kejadiannya selalu dicatat di log Home Assistant dengan kata kunci
`Reset counter terdeteksi`.

### Penghitung "hari ini" tidak kembali ke nol tengah malam

Cek atribut `next_cycle_start` pada entity penghitungnya - di situ tertulis
kapan reset berikutnya dijadwalkan. Kalau jamnya bukan yang Anda harapkan,
ubah **Jam mulai hari baru** lewat **Ubah kelompok tagihan → Periode
perhitungan**.

Penghitung juga memeriksa batas siklus setiap kali ada pembacaan baru, jadi
kalau Home Assistant sempat mati melewati tengah malam, siklus yang terlewat
langsung ditutup begitu ia hidup lagi - pemakaian kemarin tidak akan menumpuk
di penghitung hari ini.

### Sensor biaya tidak muncul

Kelompok tagihan itu belum dihubungkan ke tarif. Buka **Ubah kelompok tagihan**
dan pilih tarifnya di langkah kedua. Kalau daftarnya kosong, buat dulu tarifnya
lewat **Tambah tarif**.

### Angka biayanya terasa tidak cocok dengan pengeluaran saya

Dua kemungkinan, dan keduanya wajar:

1. **Tarifnya belum disesuaikan.** Angka bawaan Rp 1.444,70 hanya perkiraan.
   Cek atribut `active_rate_rp_per_kwh` pada sensor biaya untuk melihat tarif
   yang sedang dipakai.
2. **Sensor biaya memang bukan jumlah uang yang Anda bayar.** Ia menghitung
   nilai listrik yang dipakai dari tarif dasar; sedangkan saat beli token, uang
   Anda dipotong biaya admin dan PPJ dulu. Uang yang keluar selalu lebih besar.

### Perkiraan hari tersisa kosong terus

Cek `binary_sensor.<kelompok>_data_sufficient` dan atribut `confidence` pada
sensor perkiraan. Penyebab yang paling sering:

- **Baru dipasang.** Riwayat pemakaian belum terkumpul. Tunggu beberapa hari.
- **Pemakaian nyaris nol** (`confidence: insufficient_usage`). Tanpa pemakaian,
  "berapa hari lagi habis" memang tidak punya jawaban.
- **Belum ada token dicatat.** Perkiraan hari butuh sisa token; rata-rata
  pemakaian tetap tampil walaupun token belum dicatat.
- **Recorder dimatikan.** Perkiraan membaca riwayat jangka panjang Home
  Assistant; tanpa recorder, tidak ada yang bisa dibaca.

Perkiraan dihitung ulang setiap 30 menit, jadi perubahan tidak langsung terlihat
detik itu juga.

### Tidak menerima notifikasi

Periksa berurutan:

1. **Sudah dinyalakan?** Buka **Ubah kelompok tagihan → Notifikasi token**.
2. **Statusnya memang belum berpindah tingkat?** Pesan hanya dikirim saat
   status berubah. Cek `sensor.<kelompok>_token_status`.
3. **Sedang jam tenang?** Pesan ditahan sampai jam tenang lewat.
4. **Targetnya masih ada?** Kalau service Telegram-nya berganti nama, pengiriman
   gagal dan tercatat di log Home Assistant. Notifikasi di dalam Home Assistant
   tetap muncul sebagai cadangan.

### Notifikasi tertukar dengan automation listrik padam saya

Ubah **Awalan pesan** di langkah Notifikasi token menjadi sesuatu yang lebih
khas, misalnya `[TOKEN RUMAH]`. Awalan itu ditempelkan di depan setiap pesan
dari integrasi ini.

### Grafik histori masih kosong

Long-term statistics dihitung Home Assistant sekali per jam, jadi setelah
memasang integrasi Anda perlu menunggu satu-dua jam sebelum grafiknya berisi.
Untuk memastikan entity-nya memang terdaftar, buka **Developer Tools →
Statistics** dan cari nama entity-nya di sana.

---

## Untuk pengembang

Test dijalankan dengan `pytest` dan `pytest-homeassistant-custom-component` yang
di-pin persis ke versi Home Assistant target:

```bash
pip install pytest-homeassistant-custom-component==0.13.357
pytest
```

Versi itu memakai `homeassistant==2026.8.3`. Test **harus dijalankan di Linux
atau WSL** - Home Assistant mengimpor modul `fcntl` yang tidak ada di Windows.

Beberapa test menyalin langsung vektor uji resmi Home Assistant Core
(`test_compile_hourly_sum_statistics_total_increasing` dan
`..._small_dip`) untuk memastikan perhitungan energi kita berperilaku persis sama
dengan perhitungan statistik bawaan Home Assistant. Kalau test itu gagal,
artinya angka integrasi ini akan menyimpang dari angka Home Assistant untuk
sensor yang sama.

---

## Rencana pengembangan

| Tahap | Isi | Status |
|---|---|---|
| 1 | Pembacaan & penyeragaman sumber + config flow | **Selesai** |
| 2 | Kelompok tagihan + penghitung per periode + statistik jangka panjang | **Selesai** |
| 3 | Perhitungan biaya rupiah (tarif bisa diatur penuh) | **Selesai** |
| 4 | Pencatatan token: isi ulang, sisa kWh, kalibrasi manual | **Selesai** |
| 5 | Prediksi hari tersisa & tanggal habis | **Selesai** |
| 6 | Notifikasi Telegram bertingkat | **Selesai** |
| 7 | Dashboard | **Selesai** |
| 8 | Pembersihan data lama | Belum |

Semua tarif, ambang batas, dan periode akan selalu bisa diatur dari antarmuka -
tidak ada satu pun yang dikunci di dalam kode.
