# Catatan keputusan implementasi

Dokumen ini mencatat keputusan yang diambil **saat implementasi**, di luar apa
yang sudah tertulis di [`spec.md`](spec.md) - termasuk satu koreksi terhadap
spec itu sendiri. Urut dari yang terbaru.

Label kepercayaan mengikuti konvensi yang sama dengan `spec.md`
(VERIFIED / LIKELY / UNKNOWN / ASUMSI DESAIN).

---

## D-035 · Retensi diatur untuk seluruh integrasi, bukan per kelompok tagihan

**Tanggal**: 3 September 2026 · **Milestone 8**

Pengaturan retensi duduk di **options flow entry induk** (tombol *Configure*),
bukan di alur kelompok tagihan.

Alasannya: yang dibersihkan adalah database recorder milik Home Assistant secara
keseluruhan. "Simpan riwayat 2 tahun untuk rumah tapi 5 tahun untuk toko" bukan
kebutuhan nyata - yang nyata adalah "database saya jangan terus membengkak".
Menaruhnya per kelompok hanya menggandakan pertanyaan yang sama.

Layanan `purge_old_data` tetap **bisa** ditargetkan ke satu kelompok, karena di
sana targetnya memang masuk akal: user mungkin ingin membersihkan satu meteran
saja tanpa menyentuh yang lain.

---

## D-034 · Pembersihan data: satu-satunya jalan yang ada, dengan tiga pengaman

**Tanggal**: 3 September 2026 · **Milestone 8** · **Status: VERIFIED**

Seluruh klaim di spec N.4 saya verifikasi ulang sendiri ke Core 2026.8.3
sebelum menulis kode, karena inilah satu-satunya bagian sistem yang tidak
memakai API publik:

| Yang dicek | Hasil |
|---|---|
| `Statistics` / `StatisticsShortTerm` punya `metadata_id`, `start_ts` | Ada |
| `StatisticsMeta` punya `id`, `statistic_id` | Ada |
| FK `metadata_id` memakai `ON DELETE CASCADE` | Benar - inilah yang harus dihindari |
| `get_instance`, `session_scope`, `Recorder.async_add_executor_job` | Ada |
| `DEFAULT_MAX_BIND_VARS` | 4000 |

**Kenapa tidak ada jalan lain**: `recorder.purge_entities` sama sekali tidak
menyentuh tabel statistik, dan `recorder/clear_statistics` bersifat
semua-atau-tidak tanpa parameter waktu - ia menghapus baris `StatisticsMeta`
yang lewat cascade membuang **seluruh** riwayat entity. Keduanya tidak menjawab
"hapus yang lebih tua dari N tahun, sisanya tetap".

### Tiga pengaman

1. **Cek struktur lebih dulu.** `check_supported()` memeriksa model dan kolom
   yang dibutuhkan sebelum menyentuh apa pun. Kalau Home Assistant mengubahnya,
   integrasi gagal dengan pesan yang menyuruh user menghapus manual - dan
   `test_unsupported_schema_fails_loudly_and_deletes_nothing` membuktikan tidak
   ada satu baris pun terhapus saat itu terjadi.
2. **Baris `StatisticsMeta` tidak pernah disentuh**, supaya cascade tidak
   terpicu dan cache metadata recorder tidak korup. Dijaga oleh
   `test_metadata_row_is_kept`.
3. **Hanya entity milik integrasi ini.** Daftarnya dibangun dari *entity
   registry* (`platform == DOMAIN`), bukan dari pola nama, sehingga tidak ada
   cara entity asing ikut terjaring. `test_purge_never_touches_other_peoples_data`
   menaruh data milik entity lain dengan umur yang sama persis, lalu
   membuktikan tidak satu baris pun darinya hilang.

Ditambah satu kanari: `test_recorder_schema_is_still_what_we_expect` akan gagal
di versi Home Assistant berikutnya yang mengubah struktur ini - jauh sebelum
ada data user yang salah terhapus.

### Yang tetap harus diketahui user

Fitur ini **lebih rapuh terhadap upgrade Home Assistant** dibanding seluruh
bagian lain sistem ini, dan itu ditulis apa adanya di README - bukan
disembunyikan. Pengaturan bawaannya "Selamanya", jadi tidak ada yang terhapus
sampai user memutuskan sendiri.

Penghapusan dilakukan bertahap maksimal 1.000 baris per transaksi, masing-masing
di sesi sendiri, supaya write-lock tidak dipegang lama dan kompaksi per jam
milik recorder tidak tertahan.

---

## D-033 · Nilai pengisian siap pakai, dan pagar salah satuan dari struk PLN

**Tanggal**: 3 September 2026 · **Permintaan user, dengan struk nyata**

User selalu membeli token dengan nominal yang sama, dan struknya selalu
menghasilkan kWh yang sama. Mengetik ulang `826,50` setiap kali adalah pekerjaan
yang tidak perlu - dan satu kesempatan salah ketik yang tidak perlu.

**Yang dibuat**: daftar nilai siap pakai per kelompok tagihan, ditulis satu baris
per nilai dalam bentuk `1.000.000 = 826,50`. Nilai itu lalu bisa dipakai tiga
cara: menyebut nominalnya saja saat memanggil layanan, menekan tombol sekali
klik di dashboard, atau tetap mengetik kWh manual seperti sebelumnya.

**Kenapa dipetakan dari nominal, bukan sekadar daftar angka kWh**: itu cara user
benar-benar berpikir. Mereka tidak membeli "826,50 kWh", mereka membeli
"Rp 1.000.000". Memakai nominal sebagai kunci membuat pencatatannya sama persis
dengan apa yang terjadi di dunia nyata, dan sekaligus memberi angka nominal itu
tempat di riwayat untuk audit.

**Kenapa berupa teks banyak baris, bukan form berulang**: HA tidak punya kartu
form yang bisa menambah baris secara dinamis di config flow. Pilihannya antara
sub-flow "tambah satu lagi?" yang menjengkelkan saat mengedit, atau satu kotak
teks yang bisa disunting sekaligus. Kotak teks menang telak untuk daftar
sependek ini, dan barisan yang salah format **dilaporkan per baris**, tidak
dibuang diam-diam.

### Pagar salah satuan

Struk PLN menulis jumlah kWh dalam satuan 0,01 kWh: pada struk user tertulis
**82650 KWM**, yang di layar meteran berarti **826,50 kWh**. Menyalin 82650 apa
adanya akan membuat sisa token salah 100 kali lipat - dan karena angkanya besar,
sistem justru akan tampak "aman" selama berbulan-bulan sebelum ketahuan.

Karena itu ada batas kewajaran satu kali pengisian (20.000 kWh). Angka di
atasnya ditolak dengan saran yang menyebut angka bagi-100-nya:

> *82650 kWh terlalu besar untuk satu kali pengisian. Struk PLN sering menulis
> kWh dalam satuan 0,01 - apakah yang Anda maksud 826,50 kWh?*

Batas ini pagar salah ketik, bukan kebijakan: pembelian Rp 10 juta sekalipun
hanya menghasilkan sekitar 8.000 kWh, jadi ambangnya lapang.

### Akibatnya untuk D-032

[D-032](#d-032--tombol-catat-pengisian-tidak-dibuat-satu-klik) menyimpulkan
tombol pengisian tidak bisa dibuat karena angkanya berbeda setiap kali. Dengan
nilai siap pakai, premis itu tidak lagi berlaku untuk pembelian rutin: angkanya
memang sudah pasti, jadi tombolnya kini ada - satu tombol per nilai, dengan
dialog konfirmasi. Mengetik angka bebas tetap lewat Developer Tools.

---

## D-032 · Tombol "catat pengisian" tidak dibuat satu klik

**Tanggal**: 3 September 2026 · **Milestone 7**

Spec J meminta "tombol/shortcut ke form `add_token_topup`". Yang ada di
dashboard: **kartu petunjuk**, bukan tombol.

Alasannya jujur saja - Home Assistant tidak punya kartu bawaan yang bisa
menampilkan *form berisi angka* lalu memanggil layanan dengan isian itu. Tombol
Lovelace hanya bisa memanggil layanan dengan nilai yang sudah ditentukan
sebelumnya, sedangkan mencatat pengisian token justru butuh angka kWh yang
berbeda setiap kali.

Tiga jalan yang dipertimbangkan:

1. **Tombol dengan nilai tetap** - salah: setiap pengisian nilainya beda.
2. **Mewajibkan user membuat helper `input_number`** - berarti integrasi ini
   memaksa user membuat benda yang bukan miliknya, dan tetap butuh dua langkah.
3. **Petunjuk ke Developer Tools -> Actions** - dipilih. Form bawaannya sudah
   rapi, seluruh field-nya sudah punya penjelasan berbahasa awam dari
   `services.yaml`, dan tidak ada yang perlu disiapkan lebih dulu.

Yang **memang** dibuat sebagai tombol: dua keputusan penahanan ledger
(`abaikan` dan `anggap pemakaian nyata`), karena keduanya tidak butuh isian
angka. Keduanya memakai dialog konfirmasi dan hanya muncul saat sedang ditahan.

**Diperbarui**: sejak [D-033](#d-033--nilai-pengisian-siap-pakai-dan-pagar-salah-satuan-dari-struk-pln),
pembelian rutin **sudah punya tombol** - karena dengan nilai siap pakai,
angkanya memang tidak lagi berbeda setiap kali. Yang tetap lewat Developer Tools
hanyalah pengisian dengan angka bebas.

---

## D-031 · Hanya kartu bawaan Home Assistant, Mushroom tidak dipakai

**Tanggal**: 3 September 2026 · **Milestone 7**

Spec J membolehkan Mushroom untuk tampilan yang lebih ringkas, dengan syarat
setiap kartu kritikal punya padanan bawaan. Yang dibuat: **hanya kartu bawaan**,
tanpa Mushroom sama sekali.

Alasannya, memenuhi syarat itu dengan dua versi kartu berarti memelihara dua
jalur tampilan sekaligus - dan yang bergantung pada HACS justru yang lebih
mudah rusak saat Mushroom diperbarui. Satu jalur yang pasti jalan lebih berharga
daripada dua jalur yang salah satunya rapuh.

Dikunci oleh test `test_only_built_in_cards_are_used`, yang gagal kalau ada
kartu `custom:` masuk. User yang ingin tampilan Mushroom tetap bisa
menambahkannya sendiri di atas dashboard ini.

---

## D-030 · Dashboard dibuatkan lewat layanan, bukan disalin dari contoh statis

**Tanggal**: 3 September 2026 · **Milestone 7**

Spec L.3 menyebut dashboard sebagai "deliverable terpisah (Lovelace
config/blueprint)". Yang dibuat: layanan **Buatkan dashboard** yang menghasilkan
YAML berisi `entity_id` nyata milik instalasi user, plus satu contoh statis di
`docs/dashboard-example.yaml` sebagai rujukan.

Alasannya: `entity_id` di dashboard bergantung pada nama kelompok tagihan dan
sumber energi yang dipilih user sendiri. Contoh statis berarti user harus
mengganti belasan `entity_id` secara manual, dan satu salah ketik menghasilkan
kartu kosong **tanpa pesan kesalahan apa pun** - jenis kegagalan yang paling
membingungkan untuk ditelusuri.

Layanan ini juga menyesuaikan isinya dengan keadaan: kelompok tanpa tarif tidak
mendapat kartu biaya, kelompok tanpa token tidak mendapat kartu token, dan
periode yang tidak diaktifkan tidak muncul. Diuji lewat
`test_every_referenced_entity_actually_exists`, yang memastikan setiap entity
yang dirujuk memang ada.

Layanan ini murni membaca: ia mengembalikan teks, tidak menulis file dan tidak
menyentuh dashboard yang sudah ada. Karena itu ia lolos tinjauan daftar layanan
di `test_only_bookkeeping_services_are_registered` - yang memang sengaja gagal
lebih dulu supaya penambahan layanan baru selalu ditinjau sadar.

---

## D-029 · Notifikasi di dalam Home Assistant menyala secara bawaan

**Tanggal**: 3 September 2026 · **Milestone 6**

Selain mengirim ke tujuan pilihan user (Telegram dan sejenisnya), sistem juga
membuat **persistent notification** di dalam Home Assistant, dan itu menyala
secara bawaan.

Alasannya: peringatan token adalah hal yang tidak boleh gagal sampai. Kalau
Telegram sedang bermasalah - token bot kedaluwarsa, internet putus, service-nya
diganti nama - user tetap melihat peringatannya begitu membuka Home Assistant.
Ini jaring pengaman yang harganya nyaris nol.

Bisa dimatikan kalau user merasa terganggu.

---

## D-028 · Pesan yang tertahan jam tenang tidak butuh state tambahan

**Tanggal**: 3 September 2026 · **Milestone 6**

Saat sebuah pesan tertahan jam tenang, tidak ada antrean, penanda "tertunda",
atau timer yang perlu disimpan.

Caranya: penahanan bekerja dengan **tidak** mencatat tingkat itu sebagai sudah
terkirim. Karena syarat pengiriman adalah "tingkat sekarang berbeda dari tingkat
terakhir yang tercatat terkirim", pesan itu otomatis memenuhi syarat lagi pada
evaluasi berikutnya - dan evaluasi berjalan tiap 30 menit bersama prediksi.

Hasilnya: nol state tambahan, nol kemungkinan antrean bocor atau ganda, dan
pesannya tetap sampai begitu jam tenang lewat. Diuji eksplisit di
`test_held_message_is_sent_once_quiet_hours_end`.

---

## D-027 · Notifikasi dievaluasi bersama prediksi, bukan pada tiap perubahan state

**Tanggal**: 3 September 2026 · **Milestone 6**

Evaluasi notifikasi menumpang pada siklus perhitungan prediksi
([D-024](#d-024--prediksi-dihitung-ulang-berkala-bukan-pada-tiap-perubahan-state)):
tiap 30 menit, ditambah segera setelah ledger token berubah.

Alasannya bukan sekadar efisiensi. Status token bertumpu pada perkiraan hari
tersisa; mengevaluasi notifikasi di saat yang berbeda dari saat prediksi
diperbarui berarti mengirim pesan berdasarkan angka yang sudah basi. Menyatukan
keduanya membuat pesan selalu mencerminkan angka yang sama dengan yang dilihat
user di dashboard.

Pemicu setelah ledger berubah itulah yang membuat pesan "token sudah terisi"
datang seketika sesudah user mencatat pengisian, bukan menunggu setengah jam.

---

## D-026 · Jaminan read-only dipertajam, bukan dilonggarkan

**Tanggal**: 3 September 2026 · **Milestone 6** · **Perubahan aturan**

Sampai Milestone 5, jaminan "sistem ini tidak bisa mengendalikan listrik"
dijaga oleh test yang melarang **semua** pemanggilan service di seluruh kode.
Milestone 6 membuat larangan itu mustahil dipertahankan apa adanya: mengirim
notifikasi berarti memanggil `notify.*`.

Yang dilakukan bukan menghapus aturannya, melainkan menggantinya dengan pagar
yang lebih tepat sasaran - dan lebih kuat, karena kini berlaku juga saat
program berjalan, bukan hanya saat test:

1. **Satu pintu.** Seluruh pemanggilan service dipusatkan di `notifier.py`.
   Test `test_only_the_notifier_may_call_services` gagal kalau ada file kedua
   yang ikut memanggil service.
2. **Daftar putih domain.** `ALLOWED_SERVICE_DOMAINS` berisi `{"notify"}` saja,
   dan dikunci oleh test tersendiri.
3. **Pemeriksaan saat berjalan.** Target di luar daftar itu - `switch.turn_off`,
   `homeassistant.turn_off`, `script.*` - melempar `ForbiddenServiceError`
   **sebelum** service-nya sempat dipanggil. Diuji dengan mendaftarkan service
   `switch.turn_off` palsu lalu memastikan ia tidak pernah terpanggil.
4. Larangan kata kerja pengendali (`turn_on`, `turn_off`, `toggle`) di seluruh
   kode tetap berlaku seperti sebelumnya.

Sebelumnya jaminan itu hanya berupa pemeriksaan teks saat test. Sekarang ada
pengaman nyata di jalur eksekusi: konfigurasi yang di-edit manual sekalipun
tidak bisa membuat integrasi ini memanggil service pemutus listrik.

---

## D-025 · Ambang kWh absolut: satu field, bukan tiga (menyelesaikan ambiguitas spec)

**Tanggal**: 3 September 2026 · **Milestone 5**

Spec menyebut ambang kWh absolut dengan dua nama berbeda dan arti berbeda:
D.1 menamainya `warning_threshold_kwh` ("threshold absolut kWh, opsional selain
hari"), sedangkan I.1 memakainya sebagai `warning_threshold_kwh_very_critical`
yang memicu tingkat **sangat kritis**.

Yang diimplementasikan mengikuti **kegunaannya di I.1**: satu field bernama
`token_low_kwh_threshold`, dan sisa kWh di bawahnya langsung berarti sangat
kritis, berapa pun perkiraan harinya. Isi 0 untuk mematikannya.

Alasan memilih satu field, bukan tiga (warning/critical/very_critical versi
kWh): ambang hari sudah menangani tingkatan bertahap. Ambang kWh berperan
sebagai **jaring pengaman terakhir** yang tidak bergantung pada prediksi sama
sekali - berguna justru ketika prediksi belum tersedia. Menambah tiga field lagi
hanya menggandakan hal yang sama tanpa menambah kemampuan.

---

## D-024 · Prediksi dihitung ulang berkala, bukan pada tiap perubahan state

**Tanggal**: 3 September 2026 · **Milestone 5**

Perkiraan dihitung ulang setiap **30 menit**, ditambah segera setelah ledger
token berubah (top-up, kalibrasi, reset).

Alasannya: membaca long-term statistics menyentuh database recorder. Menghitung
ulang setiap kali meteran melapor - yang bisa terjadi tiap beberapa detik -
akan membebani database tanpa manfaat, karena "berapa hari lagi token habis"
tidak berubah bermakna dalam hitungan detik.

Perhitungan pertama dijalankan setelah platform entity selesai dipasang, karena
pembacaan statistik butuh `entity_id` sensor energi grup yang baru terdaftar di
tahap itu.

---

## D-023 · `token_status` punya nilai tambahan `hold`

**Tanggal**: 3 September 2026 · **Milestone 5**

Spec D.2 mendaftarkan lima nilai: normal / warning / critical / very_critical /
unknown. Ditambahkan satu lagi: **`hold`**, yang berlaku selama ledger token
dibekukan (lihat [D-007](#d-007--pengaman-ledger-token-saat-reset-besar)).

Alasannya: selama pembekuan, sisa token sengaja dibekukan di angka lama, jadi
perkiraan hari yang dihitung darinya belum tentu mencerminkan keadaan
sebenarnya. Menampilkan "aman" di saat itu memberi rasa aman palsu; menampilkan
"sangat kritis" memberi panik palsu. `hold` mengatakan apa adanya: sistem sedang
menunggu keputusan Anda.

Ini juga menyiapkan Notification Engine di Milestone 6, supaya notifikasi tidak
dikirim berdasarkan angka yang sedang dibekukan.

---

## D-022 · Rata-rata pemakaian harian sengaja tanpa `device_class`

**Tanggal**: 3 September 2026 · **Milestone 5**

`sensor.<mu>_average_daily_usage` memakai satuan `kWh/d` tanpa `device_class`.

Spec D.2 memberinya `device_class: energy`, tapi itu tidak sah dua kali: Core
2026.8.3 tidak mengizinkan `energy` dengan `state_class: measurement` (lihat
[D-016](#d-016--sisa-token-memakai-energy_storage-bukan-energy-koreksi-spec-d2)),
dan secara arti pun keliru - ini **laju** (energi per hari), bukan energi yang
menumpuk. Home Assistant belum punya device_class untuk laju energi, jadi tidak
memakai satu pun adalah pilihan yang paling jujur.

`state_class: measurement` tetap dipasang, sehingga rata-ratanya tetap masuk
statistik jangka panjang dan bisa digrafikkan.

---

## D-021 · Statistik dibaca lewat tipe `change`, bukan menghitung selisih `sum` sendiri

**Tanggal**: 3 September 2026 · **Milestone 5** · **Status: VERIFIED**

Konsumsi per hari/jam diambil dengan `statistics_during_period(...,
types={"change"})`. Diverifikasi ke source Core 2026.8.3
(`components/recorder/statistics.py`): `change` memang dihitung sebagai
`_sum - prev_sum`, yaitu persis konsumsi dalam periode itu.

Alternatifnya - membaca `sum` lalu menghitung selisihnya sendiri - berarti
menduplikasi logika yang sudah ada di Core, termasuk penanganan pergantian
siklus dan periode yang kosong. Memakai `change` menghilangkan seluruh kelas
bug itu.

Seluruh akses tetap lewat API resmi dan dijalankan di thread recorder
(`get_instance(hass).async_add_executor_job`), sesuai spec L.2 - tidak ada query
SQL manual di mana pun.

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

**Terpasang di Milestone 5**, lengkap dengan satu nilai tambahan `hold` (lihat
[D-023](#d-023--token_status-punya-nilai-tambahan-hold)).

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
