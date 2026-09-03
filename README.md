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
6. [Kenapa angkanya beda dengan aplikasi meteran?](#kenapa-angkanya-beda-dengan-aplikasi-meteran)
7. [Menambah sumber kedua, ketiga, dst](#menambah-sumber-kedua-ketiga-dst)
8. [Troubleshooting](#troubleshooting)
9. [Untuk pengembang](#untuk-pengembang)
10. [Rencana pengembangan](#rencana-pengembangan)

---

## Apa yang sudah bisa dipakai sekarang

Integrasi ini dibangun bertahap. Yang **sudah selesai dan bisa dipakai**:

- **Pembacaan & penyeragaman sumber energi.** Anda memetakan sensor milik
  meteran/colokan pintar Anda, lalu integrasi ini menghasilkan sensor baru yang
  satuannya seragam (kWh, W, V, A, Hz) dan angkanya aman dari reset counter.
- **Deteksi sumber putus** dengan masa tenggang yang bisa Anda atur.

Yang **belum** (lihat [Rencana pengembangan](#rencana-pengembangan)): perhitungan
biaya rupiah, pencatatan token, prediksi hari tersisa, notifikasi Telegram, dan
dashboard.

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
| 2 | Penghitung per periode (jam/hari/minggu/bulan/tahun) + statistik jangka panjang | Belum |
| 3 | Perhitungan biaya rupiah (tarif bisa diatur penuh) | Belum |
| 4 | Pencatatan token: isi ulang, sisa kWh, kalibrasi manual | Belum |
| 5 | Prediksi hari tersisa & tanggal habis | Belum |
| 6 | Notifikasi Telegram bertingkat | Belum |
| 7 | Dashboard | Belum |
| 8 | Pembersihan data lama | Belum |

Semua tarif, ambang batas, dan periode akan selalu bisa diatur dari antarmuka -
tidak ada satu pun yang dikunci di dalam kode.
