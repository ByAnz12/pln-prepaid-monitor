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
| `const.py` → `PLATFORMS` | Dikunci ke `sensor`, `binary_sensor`, `number`, `button`, `select`, `text`, `date` |
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

### Rencana dulu, kerjakan sesudah disetujui

Permintaan pemilik, berlaku untuk **setiap** tugas:

> Setiap saya beri tugas selalu buat perencanaan terlebih dahulu, jika sudah
> saya approve baru lanjut untuk mengerjakan.

Jadi urutannya: baca kode secukupnya untuk mengerti masalahnya → sampaikan
rencana → **berhenti dan tunggu** → baru kerjakan.

Menyelidiki, membaca berkas, dan menjalankan test **boleh** dilakukan sebelum
persetujuan — rencana yang disusun tanpa membaca kode hanya tebakan, dan
pemilik sudah menegaskan tidak mau ditebak-tebak. Yang harus menunggu adalah
**perubahan**: mengedit berkas, commit, push, menaikkan versi.

Kalau ada bagian perintah yang ambigu, jangan diam-diam memilih tafsiran
sendiri. Sebutkan ambiguitasnya, beri rekomendasi beserta alasannya, dan
biarkan pemilik yang memutuskan.

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

Di mesin pemilik, venv-nya sudah siap:

```bash
wsl -d Ubuntu -e bash -lc 'cd "/mnt/e/HOME ASSISTANT" && ~/.venvs/pln/bin/python -m pytest'
```

Di clone yang masih kosong (misalnya sesi Claude Code di web), venv-nya harus
dibuat dulu. **Python 3.14.2 ke atas** — `homeassistant==2026.8.3` menolak yang
lebih tua, dan `pip` pada Python lama akan bilang versinya "tidak ada" padahal
yang salah adalah interpreternya:

```bash
uv python install 3.14.2          # uv < 0.9 belum kenal 3.14.2; perbarui dulu
uv venv --python 3.14.2 .venv
uv pip install --python .venv/bin/python -r requirements_test.txt
.venv/bin/python -m pytest
```

523 test saat ini. Jangan menambah fitur tanpa test yang menjaganya.

### Test bukan satu-satunya gerbang: ada hassfest

`.github/workflows/validate.yml` menjalankan **hassfest** (milik tim Home
Assistant) dan **hacs/action**. Keduanya memeriksa hal yang tidak disentuh
pytest sama sekali: bentuk `services.yaml`, `strings.json`, `translations/`,
`icons.json`, dan `manifest.json`.

Pernah kejadian: hassfest merah di `main` selama empat commit berturut-turut
tanpa ada yang sadar, karena `release.yml` hanya menunggu pytest. Lihat D-053.

Jadi sesudah menyentuh salah satu berkas di atas, periksa juga hasil workflow
**Validate**, bukan cuma **Tests**.

### Test yang berharga menangkap kesalahan yang tidak terlihat salah

Pola yang dipakai di proyek ini: kunci hal yang **angkanya tetap masuk akal
walau salah**. Contohnya periode berjalan yang diam-diam menyeret rata-rata
turun, atau label tombol yang menyebut angka berbeda dari yang tercatat.

Template Jinja di dashboard **wajib benar-benar dirender** di test — termasuk
saat datanya kosong. Template yang salah hanya tampil sebagai kotak merah tanpa
pesan berguna.

### Catat keputusan, jangan menyimpang diam-diam

`docs/decisions.md` — 56 keputusan, D-001 sampai D-056. Setiap penyimpangan dari
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
`version` di `manifest.json` berubah — sesudah seluruh test lulus **dan**
hassfest hijau (D-053).

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

Sesudah mengubah dashboard, bangkitkan ulang `docs/dashboard-example.yaml` —
jangan mengeditnya dengan tangan:

```bash
PLN_WRITE_EXAMPLE=1 .venv/bin/python -m pytest tests/test_dashboard_example.py
```

Skenarionya ada di `tests/test_dashboard_example.py`, dan test yang sama gagal
kalau berkas itu tidak lagi sama dengan keluaran kode (D-053).

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

**15 test recorder bisa gagal di mesin sendiri, bukan karena kodenya.** Pada
sebagian build Python 3.14, `create_autospec` mengevaluasi anotasi yang namanya
cuma ada di blok `TYPE_CHECKING` (PEP 649), jadi fixture `recorder_mock` mati
dengan `NameError: name 'Recorder' is not defined`. Di GitHub Actions ini tidak
terjadi. Kalau muncul: pakai plugin pytest sekali pakai yang membuat
`unittest.mock._get_signature_object` jatuh ke
`annotationlib.Format.FORWARDREF` saat `NameError` — **jangan** dimasukkan ke
repo, dan jangan pula disangka `test_retention.py` atau `test_statistics.py`
yang rusak.
