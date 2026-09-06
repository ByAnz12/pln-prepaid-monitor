# Petunjuk kerja untuk proyek ini

Integrasi custom Home Assistant: **PLN Prepaid Energy & Cost Monitor**
(domain `pln_prepaid_monitor`). Memantau listrik prabayar PLN — pemakaian,
biaya, sisa token, prediksi habis, dan notifikasi.

Repo: <https://github.com/ByAnz12/pln-prepaid-monitor>

---

## Aturan yang tidak boleh dilanggar

### 1. Sistem ini tidak boleh bisa memutus atau menyalakan listrik

Ini permintaan pemilik, kata demi kata:

> Sistem ini HANYA monitor, hitung, prediksi, dan notifikasi. JANGAN PERNAH
> mendaftarkan platform switch atau memanggil service apa pun yang bisa
> memutus/menyalakan listrik — termasuk entity switch/relay milik device sumber.

Wujudnya di kode:

| Berkas | Isinya |
|---|---|
| `const.py` → `FORBIDDEN_PLATFORMS` | `switch`, `climate`, `cover`, `fan`, `light`, `lock`, `siren`, `vacuum`, `valve`, `water_heater`, `humidifier` — terlarang selamanya |
| `const.py` → `PLATFORMS` | Dikunci ke `sensor`, `binary_sensor`, `number`, `button`, `select`, `text` |
| `notifier.py` | **Satu-satunya** berkas yang boleh memanggil service, dan hanya domain `notify` |
| `tests/test_readonly_guarantee.py` | Menggerakkan **seluruh** entity lalu memastikan tidak ada relay yang bergerak |

Jaminannya **berbasis perilaku, bukan nama platform** (lihat D-039, D-047).
Kalau perlu menambah platform baru, tanyakan dulu ke pemilik dan perluas test
perilaku itu — jangan cukup mengubah daftarnya.

Config flow juga tidak boleh menawarkan entity domain `switch`/`number`/`select`
milik perangkat sumber sebagai pilihan.

### 2. Tidak ada logika khusus merek atau model

Tongou, Tomzn, Tuya, Zigbee — semua sumber ditangani lewat pemetaan `entity_id`
secara generik, apa pun protokol di belakangnya. Jangan pernah menulis cabang
`if merek == ...` di mana pun.

### 3. Tarif, ambang, dan periode selalu bisa diatur

Tidak ada satu pun yang dikunci di kode atau template. Nilai bawaan boleh ada,
tapi harus bisa diubah dari antarmuka.

---

## Cara kerja yang diminta pemilik

### Verifikasi ke source, jangan mengandalkan ingatan

Setiap API Home Assistant **diperiksa langsung ke source code yang terpasang**
sebelum dipakai. Cara ini sudah menemukan banyak kekeliruan nyata:
`min_ha_version` bukan kunci manifest yang sah, `monetary` hanya menerima
`total`, `energy` tidak menerima `measurement`, folder brands sudah legacy.

```bash
wsl -d Ubuntu -e bash -lc 'P=$(~/.venvs/pln/bin/python -c "import homeassistant,os;print(os.path.dirname(homeassistant.__file__))"); grep -n "..." "$P"/...'
```

Satu-satunya bagian yang **tidak bisa** diverifikasi begini: kartu HACS
(Mushroom, apexcharts) — kodenya JavaScript pihak ketiga. Itu harus disebutkan
apa adanya kalau menyentuhnya.

### Menjalankan test

Wajib di Linux/WSL — Home Assistant mengimpor `fcntl` yang tidak ada di Windows.

```bash
wsl -d Ubuntu -e bash -lc 'cd "/mnt/e/HOME ASSISTANT" && ~/.venvs/pln/bin/python -m pytest'
```

430 test saat ini. Jangan menambah fitur tanpa test yang menjaganya.

### Test yang berharga menangkap kesalahan yang tidak terlihat salah

Pola yang dipakai di proyek ini: kunci hal yang **angkanya tetap masuk akal
walau salah**. Contohnya periode berjalan yang diam-diam menyeret rata-rata
turun, atau label tombol yang menyebut angka berbeda dari yang tercatat.

Template Jinja di dashboard **wajib benar-benar dirender** di test — termasuk
saat datanya kosong. Template yang salah hanya tampil sebagai kotak merah tanpa
pesan berguna.

### Catat keputusan, jangan menyimpang diam-diam

`docs/decisions.md` — 52 keputusan, D-001 sampai D-052. Setiap penyimpangan dari
blueprint awal, setiap keputusan yang bisa dipertanyakan orang lain nanti,
ditulis beserta **alasannya**. Tambahkan entri baru di atas yang terakhir.

### Bahasa

Komentar, docstring, commit message, README, dan seluruh teks antarmuka:
**bahasa Indonesia**. Kode dan nama test tetap bahasa Inggris.

Teks antarmuka disediakan lengkap dalam dua bahasa (`id` dan `en`) di
`strings.json` + `translations/`. Jangan sampai ada kunci yang tertinggal.

### Setiap isian, entity, dan layanan wajib punya penjelasan awam

Bukan sekadar nama. Pemilik meminta ini sejak awal, dan itu alasan
`data_description` di config flow panjang-panjang.

---

## Git dan rilis

### Push otomatis, tanpa bertanya

Setelah revisi selesai dan test lulus: `git add` → `commit` → `push` langsung.
Pemilik sudah memberi izin berdiri untuk ini.

**Tetap tanyakan dulu** untuk aksi yang merusak riwayat: `push --force`,
menghapus branch, rewrite history. Itu bukan push rutin.

### Menaikkan versi = perintah menerbitkan

`.github/workflows/release.yml` membuat rilis GitHub **sendiri** ketika angka
`version` di `manifest.json` berubah — sesudah seluruh test lulus.

Jadi menaikkan versi bukan perubahan biasa. Kalau ada pekerjaan yang belum
pantas dirilis, jangan sentuh `version`.

HACS memasang **rilis terakhir, bukan branch** — tanpa rilis baru, perbaikan
yang sudah dipush tidak pernah sampai ke pengguna.

---

## Dashboard

Dibangkitkan lewat layanan `generate_dashboard`, bukan disalin dari contoh
statis. Tiga tata letak: `sections` (bawaan, bisa drag & drop), `sections_hacs`
(perlu Mushroom + apexcharts), `masonry` (klasik).

**Hanya kartu bawaan Home Assistant** pada dua tata letak pertama — tidak ada
dependency HACS. Itu prinsip spec J dan sudah dikunci test.

Urutan bagiannya **dikunci test** ke susunan yang pemilik rapikan sendiri:
Ringkasan → Token → Pemakaian & biaya → Grafik → Analisa → Pengaturan. Jangan
mengubahnya tanpa diminta; kalau berubah, pemilik harus menata ulang dari awal
dan tidak akan tahu kenapa.

Sesudah mengubah dashboard, bangkitkan ulang `docs/dashboard-example.yaml`
lewat kode aslinya — jangan mengeditnya dengan tangan.

---

## Jebakan yang sudah pernah menjatuhkan pekerjaan di sini

**Heredoc memakan backslash.** Menulis patch Python lewat `cat << 'EOF'` merusak
`\n` dan `\x00` di dalam string. Untuk isi yang mengandung backslash, pakai
tool Edit atau Write — bukan heredoc.

**Satu blok tidak cocok = seluruh patch batal.** Skrip patch yang memakai
beberapa `assert old in s` lalu menulis di akhir akan kehilangan *semua*
perubahan kalau satu blok meleset. Pernah terjadi, dan ketahuan lewat test.

**Kartu `conditional` tetap dirender saat tersembunyi.** Yang diatur hanya
tampil atau tidaknya. Template di dalamnya harus tahan nilai kosong.

**`entity_id` mengikuti bahasa Home Assistant.** `id` ada di `NATIVE_ENTITY_IDS`,
jadi instalasi berbahasa Indonesia menghasilkan `sensor.pln_rumah_total_energi`.
Test jangan mencocokkan potongan nama entity — cocokkan lewat perannya.

**Menulis konfigurasi memuat ulang entry.** `async_update_subentry` memicu
reload, dan objek runtime yang lama ikut mati. Kosongkan isian *sebelum*
menulis konfigurasi, bukan sesudahnya.
