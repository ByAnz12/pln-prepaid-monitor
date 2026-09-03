# PLN Prepaid Energy & Cost Monitor — Technical Design Specification

**Dokumen**: Blueprint arsitektur untuk sistem monitoring energi & biaya listrik prepaid PLN di Home Assistant
**Status**: Design spec — belum ada implementasi YAML/kode
**Tanggal**: 2 September 2026
**Ditujukan untuk**: implementasi lanjutan oleh Claude Code

**Skema label kepercayaan** yang dipakai di seluruh dokumen ini:
- **[VERIFIED]** — dikonfirmasi dari dokumentasi resmi/sumber primer (URL disertakan).
- **[LIKELY]** — didukung sumber sekunder yang konsisten (forum, GitHub issue, artikel), belum dokumentasi resmi.
- **[UNKNOWN]** — tidak ditemukan, atau tidak bisa diverifikasi. Tidak boleh dianggap benar tanpa pengecekan ulang saat implementasi.
- **[ASUMSI DESAIN]** — keputusan desain saya yang *tidak* diklaim sebagai fakta terverifikasi, dan sengaja dibuat *configurable* supaya salah asumsi tidak merusak sistem.

Semua klaim tentang perilaku Home Assistant dan PLN di bawah ini sudah melalui riset web (bukan ditebak dari ingatan model), kecuali yang saya tandai eksplisit sebagai pengetahuan umum yang perlu diverifikasi ulang saat implementasi.

---

## A. Executive Summary

### Rekomendasi arsitektur

Bangun ini sebagai **satu custom integration Home Assistant** (bukan sekadar kumpulan template YAML), dengan domain contoh `pln_prepaid_monitor`. Alasannya, bukan sekadar preferensi:

1. Anda meminta sistem yang bisa menambah **energy source, aggregate, dan billing group baru tanpa membongkar sistem**. Dengan YAML/helper murni, menambah source baru berarti menambah blok `input_number`/`template` baru secara manual per source — ini *tidak* scalable dan rawan salah copy-paste antar source. Dengan custom integration ber-**config flow + options flow**, source baru ditambahkan lewat UI (Settings → Devices & Services → tambah entry), disimpan di `.storage`, dan seluruh entity turunan (normalisasi, cost, token, prediction, notification) dibuat otomatis oleh integration.
2. Config entry adalah tempat idiomatik HA untuk **state konfigurasi yang harus survive restart dan editable dari UI** — ini pola arsitektur developer HA yang umum dipakai untuk custom integration modern **[ASUMSI DESAIN berbasis pola arsitektur HA umum — tidak ada satu dokumen resmi yang membandingkan config-entry vs helper secara eksplisit untuk kasus ini; ditandai UNKNOWN di riset]**. `input_number`/`input_datetime` helper juga persist otomatis lintas restart **[VERIFIED — home-assistant.io/integrations/input_number/, input_datetime/]**, jadi ini valid sebagai *alternatif ringan* (Track B, lihat di bawah), tapi tidak mendukung penambahan source dinamis tanpa edit YAML.
3. Custom integration bisa membuat entity dinamis per source/billing group dengan `unique_id` konsisten, device registry grouping, dan `DataUpdateCoordinator` — pola resmi HA untuk integrasi yang menggabungkan banyak sumber data.

**Track A (Direkomendasikan, [DIKONFIRMASI user 2 Sept 2026]): Custom Integration.** Spesifikasi ini ditulis untuk Track A, dengan satu syarat tambahan dari user: seluruh UI konfigurasi wajib disertai penjelasan rinci berbahasa awam (lihat L.2, L.1) — "fully configurable" tidak boleh berarti "hanya paham developer".

**Track B (Alternatif ringan, disebut sebagai fallback): YAML packages + helpers + trigger-based template sensors**, satu file `packages/pln_source_<nama>.yaml` per source. Valid untuk 1–2 source dan pengguna yang tidak mau maintain Python custom component, tapi setiap source baru = edit YAML baru, dan logika kalkulasi (cost/token/prediction) harus diduplikasi manual per billing group. Track B **tidak dibahas detail** di dokumen ini kecuali disebut sebagai catatan; jika Anda memilih Track B, sebagian besar Data Model & Calculation Logic di bawah tetap berlaku secara konseptual, hanya vehicle implementasinya beda.

### Prinsip non-negotiable yang dipertahankan di seluruh desain

- **Read-only terhadap listrik**: sistem ini tidak pernah memanggil service `switch.turn_off`/`turn_on` atau service pemutus daya apa pun, walau physical device (mis. Tongou) punya kemampuan relay/switching. Ini murni MONITOR → CALCULATE → PREDICT → NOTIFY.
- **cumulative kWh sebagai source of truth**, power W hanya fallback/estimasi (prinsip #4 Anda) — dijabarkan di Bagian F.
- **Tidak ada nominal token yang diasumsikan = kWh** — dijabarkan di Bagian G, termasuk temuan riset soal biaya admin + PPJ.
- **Semua tarif/threshold/periode adalah parameter konfigurasi**, tidak pernah hardcoded di template/kode.

### Risiko desain terbesar yang perlu Anda putuskan sebelum implementasi

Lihat Bagian M di akhir dokumen — jangan lewati, karena ada 1 asumsi soal perilaku token PLN yang sebaiknya dikonfirmasi dengan meter fisik Anda sebelum Claude Code mengimplementasikan Token Engine.

---

## B. Hardware & Domain Research Findings

### B.1 Tongou TO-Q-SYS-JWT (hanya sebagai *salah satu contoh* energy source — tidak jadi dependency)

| Klaim | Confidence | Sumber |
|---|---|---|
| DIN-rail smart circuit breaker + energy meter 1-fasa, 50A, tanpa CT eksternal, ada LCD | VERIFIED | [chayo.tech product page](https://www.chayo.tech/product/single-phase-din-rail-smart-meter/), [devices.esphome.io](https://devices.esphome.io/devices/tongou-to-q-sys-jwt-power-meter/) |
| Konektivitas: WiFi + Tuya cloud, pairing via app Smart Life/Tuya Smart; chip WiFi BK7231T/N (modul "CBU"), MCU metering terpisah HC32F460, komunikasi UART Tuya-MCU protocol | VERIFIED/LIKELY | [HA community thread — model sibling TO-Q-SY1-JWT](https://community.home-assistant.io/t/tongou-din-electricity-monitors-wifi-model-to-q-sy1-jwt/598443), [dshcherb/TO-Q-SYS-JWT-meter-esphome](https://github.com/dshcherb/TO-Q-SYS-JWT-meter-esphome) |
| Tidak ada varian Zigbee resmi ditemukan | UNKNOWN (tidak ditemukan bukti sebaliknya) | — |
| Terdaftar eksplisit di integrasi Tuya resmi HA | UNKNOWN | tidak ada di dokumentasi resmi home-assistant.io/integrations/tuya/ |
| `tuya-local` (make-all/tuya-local) punya product ID cocok untuk model sibling TO-Q-SY1-JWT / AT-Q-SY1-JWT (`jdj6ccklup7btq3a`), auto-match ~94% ke config "smartplugv2_energyv2" | LIKELY | [tuya-local issue #909](https://github.com/make-all/tuya-local/issues/909) |
| Ada laporan throttle update jadi per-jam pada mode cloud/polling, dan regresi HA 2026.4.0 yang menyebabkan relay mati sendiri (fixed dengan rollback ke 2026.3.3) | LIKELY | [tuya-local #3480](https://github.com/make-all/tuya-local/issues/3480), [#4965](https://github.com/make-all/tuya-local/issues/4965) |
| Firmware ESPHome lokal (non-cloud) tersedia, tetap pakai kalibrasi metering MCU asli, dibutuhkan akses fisik (buka casing, bor rivet) | VERIFIED | [dshcherb/TO-Q-SYS-JWT-meter-esphome](https://github.com/dshcherb/TO-Q-SYS-JWT-meter-esphome) |
| Datapoint (DP) map lengkap khusus model *SYS* (bukan SY1) | UNKNOWN — tidak ada tabel DP resmi tunggal yang terverifikasi untuk model persis ini | — |

**Kesimpulan untuk arsitektur**: apa pun jalur integrasi yang Anda pakai untuk Tongou (Tuya cloud, `tuya-local`, atau ESPHome lokal), yang penting untuk sistem generic ini hanyalah: perangkat tersebut pada akhirnya menghasilkan entity `sensor` untuk energi kumulatif (kWh), daya (W), tegangan (V), arus (A), dan opsional frekuensi (Hz) + entity availability. Sistem ini hanya butuh Anda **memetakan entity_id tersebut** ke konfigurasi source — tidak ada logic Tongou-specific di mana pun dalam desain ini.

**[Dikonfirmasi user]**: saat mengecek entity yang diekspos MCB Anda di HA, opsi "total energy" punya beberapa pilihan entity, sedangkan opsi "daya" hanya menampilkan daya yang sedang dipakai saat itu (instantaneous). Ini menguatkan asumsi Source Normalization di D.1: `power_entity_id` (measurement, instantaneous) dan `energy_entity_id` (cumulative) memang dua entity terpisah pada hardware Anda — sesuai desain. Catatan: temuan ini **tidak** menjawab pertanyaan soal perilaku saat counter energy itu sendiri mengalami reset (lihat F.2/M.3, yang tetap berstatus open risk).

### B.4 Environment Home Assistant Milik User (dikonfirmasi user, 2 Sept 2026)

| Komponen | Versi |
|---|---|
| Installation method | Home Assistant OS |
| Core | 2026.8.3 |
| Supervisor | 2026.08.0 |
| Operating System | 18.2 |
| Frontend | 20260729.7 |
| Companion app | 2026.7.5 (2026.2744) |

**Implikasi untuk implementasi**: karena instalasi berbasis **Home Assistant OS + Supervisor**, HACS bisa dipasang dengan mudah (Add-on Store atau installer resmi HACS) dan custom_components berjalan di Python runtime bawaan HA Core (bukan venv terpisah) — dependency tambahan di `manifest.json` (`requirements`) diresolusi otomatis oleh HA saat integrasi dimuat. Core 2026.8.3 berada **setelah** linimasa removal legacy template (2026.6) yang disebut di B.3 — menaikkan keyakinan bahwa syntax lama sudah tidak berlaku di instance ini, tapi linimasa itu sendiri tetap **[LIKELY]**, bukan dari changelog resmi yang saya baca langsung. Claude Code tetap wajib melakukan satu sanity check langsung (cek breaking-changes changelog resmi di github.com/home-assistant/core/releases untuk rilis 2026.6, atau cek source `homeassistant/helpers/template.py`/`config_validation` pada environment aktual) sebelum menulis kode yang mengasumsikan ini pasti.

### B.2 Domain PLN Prepaid (Token)

| Klaim | Confidence | Sumber |
|---|---|---|
| Nominal Rupiah dipotong **biaya admin** (flat, tergantung channel bayar, umumnya Rp 1.500–3.000+) dan **PPJ** (Pajak Penerangan Jalan, persentase, ditentukan pemda, umum di kisaran 3%–10%, beda per daerah & golongan tarif) sebelum dibagi tarif Rp/kWh | LIKELY (konsisten di banyak sumber konsumen/media, bukan kutipan langsung pln.co.id) | [tariflistrik.com](https://tariflistrik.com/blog/memahami-token-listrik-prabayar-cara-kerja-dan-perhitungan), [fastpay.co.id](https://www.fastpay.co.id/blog/begini-rumus-cara-menghitung-kwh-token-listrik-pln.html), [tempo.co](https://www.tempo.co/ekonomi/cara-menghitung-besaran-kwh-dari-pembelian-token-listrik-diskon-50-persen--1189291) |
| Formula umum: `kWh = (Nominal − Biaya Admin − PPJ) / Tarif`, dengan `PPJ = (Nominal_setelah_admin × PPJ%) / (1 + PPJ%)` | LIKELY | sumber sama di atas |
| **Tidak ada API resmi PLN** untuk membaca sisa kWh real-time. Beberapa repo GitHub tidak resmi ada tapi sifatnya rapuh/tidak terverifikasi (satu bahkan ditandai DEPRECATED) | UNKNOWN (API resmi) / LIKELY (API tidak resmi rapuh) | [Pengendali-API/PLN-Mobile](https://github.com/Pengendali-API/PLN-Mobile), [sandrocods/API-Cek](https://github.com/sandrocods/API-Cek), [tegalan/tagihan-pln](https://github.com/tegalan/tagihan-pln) (deprecated) |
| **Tidak ada integrasi/blueprint HA khusus PLN prepaid** yang ditemukan — ini genuine gap di komunitas | VERIFIED (negative finding dari pencarian forum resmi HA) | [community.home-assistant.io search](https://community.home-assistant.io/t/indonesia-tariff-energy-rate/511622) (hanya bahas tarif postpaid) |
| Tidak ada formula/window rata-rata standar untuk "hari tersisa" di komunitas Indonesia — pendekatan informal (`sisa kWh / estimasi rata-rata harian`) | LIKELY | [medcom.id](https://www.medcom.id/properti/news-properti/Gbm0qGxN-isi-token-50-ribu-habis-berapa-hari-ini-jawabannya) |
| Contoh tarif 2026 per golongan daya (indikatif, **bukan** dari pln.co.id langsung) | LIKELY, perlu verifikasi ulang saat implementasi karena tarif berubah berkala | agregator berita tarif, mis. [journalarta.com](https://journalarta.com/news/2026/08/25/daftar-tarif-listrik-pln-24-30-agustus-2026/) |

**Implikasi desain langsung**:
1. Tarif Rp/kWh, biaya admin, dan PPJ% **wajib configurable per instalasi**, tidak pernah hardcoded — ini sudah sejalan dengan permintaan Anda, dan riset mengonfirmasi variasinya nyata (beda kota, beda golongan, berubah dari waktu ke waktu).
2. Karena tidak ada API resmi, **entry token PLN wajib manual** (via service call/UI helper), bukan pull otomatis. Tidak ada cara aman untuk auto-fetch sisa kWh dari PLN.
3. Karena tidak ada konvensi window rata-rata baku, prediksi day-remaining harus **eksplisit soal window mana yang dipakai** dan level kepercayaannya (lihat Bagian H) — jangan menyajikan angka presisi seolah pasti.

### B.3 Home Assistant — mekanisme inti yang dipakai desain ini

| Klaim | Confidence | Sumber |
|---|---|---|
| `device_class: energy` berpasangan dengan `state_class: total` atau `total_increasing`; `power/voltage/current/frequency` hanya valid dengan `state_class: measurement` (core menolak `total_increasing` pada `power`); `monetary` valid dengan `measurement`/`total`, **bukan** `total_increasing` | VERIFIED (unit) / LIKELY (aturan pairing, dari core validation error yang dikutip komunitas) | [sensor integration docs](https://www.home-assistant.io/integrations/sensor/), [developer sensor entity docs](https://developers.home-assistant.io/docs/core/entity/sensor/), [community thread](https://community.home-assistant.io/t/entity-is-using-state-class-total-increasing-which-is-impossible-considering-device-class-power/718182) |
| `total_increasing`: menghitung running sum dari selisih state, dan menangani reset counter (nilai turun) dengan melanjutkan akumulasi dari nilai baru tanpa menghitung lompatan negatif sebagai konsumsi | [Medium confidence — pengetahuan umum HA, sebaiknya diverifikasi ulang terhadap dokumentasi versi HA yang dipakai saat implementasi] | perlu re-verify di implementasi |
| **Long-term statistics** (tabel `statistics`, agregat per jam) **tidak pernah di-purge**, sedangkan `states`/`statistics_short_term` di-purge (default `purge_keep_days`) — ini sumber yang lebih tepat untuk rollup hour/day/week/month/year dibanding `utility_meter` untuk keperluan *pelaporan historis* | VERIFIED | [HA Data Science Portal — statistics](https://data.home-assistant.io/docs/statistics/), [recorder docs](https://www.home-assistant.io/integrations/recorder/) |
| `utility_meter`: mendukung cycle `quarter-hourly/hourly/daily/weekly/monthly/bimonthly/quarterly/yearly` + custom cron offset, dan **multiple tariffs** (auto-generate 1 sensor per tarif + `select` entity switcher) — tepat untuk entity "current period total" yang live di dashboard/otomasi, tapi bukan pengganti statistics untuk histori jangka panjang | VERIFIED | [utility_meter docs](https://www.home-assistant.io/integrations/utility_meter/) |
| `history_stats` **bukan** alat agregasi numerik (bukan untuk sum kWh) — dia untuk durasi/rasio state biner/kategorikal. Jangan dipakai untuk cost/energy rollup | VERIFIED | [history_stats docs](https://www.home-assistant.io/integrations/history_stats/) |
| Energy dashboard native mendukung cost tracking (static price / entity price) dan auto-generate companion `_cost` sensor, tapi untuk tarif custom bertingkat (seperti kombinasi admin+PPJ+tarif dinamis) pola yang didokumentasikan komunitas adalah template sensor manual (`energy × price + fees`) | LIKELY | [home-assistant/core issue #124167](https://github.com/home-assistant/core/issues/124167), [community thread](https://community.home-assistant.io/t/energy-dashboard-how-ha-should-keep-track-of-the-costs/681490) |
| Template sensor modern: key `template:` top-level (sejak 2021.10), **legacy `platform: template` di bawah `sensor:` sudah deprecated per 2025.12 dan dihapus penuh di 2026.6** — pada tanggal dokumen ini (Sept 2026) berarti sudah tidak tersedia lagi | VERIFIED (syntax) / LIKELY (linimasa deprecation — **wajib dicek ulang terhadap versi HA aktual Anda saat implementasi**) | [template integration docs](https://www.home-assistant.io/integrations/template/), [community deprecation announcement](https://community.home-assistant.io/t/deprecation-of-legacy-template-entities-in-2025.12/955562) |
| **Trigger-based template** (`trigger:` alih-alih reactive state template) hanya recompute saat trigger tertentu terpenuhi (time pattern, event, state tertentu), bukan setiap kali entity terkait berubah — cocok untuk sensor kalkulasi mahal seperti cost/prediction | VERIFIED | [template integration docs](https://www.home-assistant.io/integrations/template/) |
| `input_number`/`input_datetime`/`input_text`/`input_boolean` persist otomatis lintas restart (kecuali `initial:` di-set di YAML, yang memaksa reset tiap restart) | VERIFIED | [input_number docs](https://www.home-assistant.io/integrations/input_number/), [input_datetime docs](https://www.home-assistant.io/integrations/input_datetime/) |
| HA punya integration `integration` (Riemann sum integral) untuk konversi power (W) → energy (kWh) ketika hanya ada sensor power | [Medium confidence — dikenal luas sebagai fitur HA core, tidak diverifikasi ulang lewat browsing khusus di sesi ini; cek `home-assistant.io/integrations/integration/` saat implementasi] | perlu re-verify |

**Kesimpulan arsitektur dari riset ini**: source sensor dinormalisasi ke `device_class: energy` + `state_class: total_increasing`; histori hour/day/week/month/year diambil dari **long-term statistics** (bukan duplikasi di `utility_meter`); `utility_meter`-style counter dipakai hanya untuk entity "live current period" yang dibaca dashboard; cost dibangun via **trigger-based template**, bukan legacy `platform: template` (sudah tidak ada); nilai konfigurasi tersimpan di config entry (Track A) atau `input_number`/`input_datetime` (Track B), keduanya persist lintas restart.

---

## C. Generic Architecture

```
┌─────────────────────┐
│   ENERGY SOURCES     │  Entity mentah dari integrasi apa pun
│  (Tuya/localtuya/    │  (Tongou, sub-meter lain, solar inverter, dst)
│   ESPHome/Zigbee/…)  │  User memetakan entity_id via config flow
└──────────┬───────────┘
           │
┌──────────▼───────────┐
│ SOURCE NORMALIZATION  │  Validasi device_class/state_class,
│ (per Energy Source)   │  buffer unavailable, hold-last-value,
│                       │  hasilkan skema kanonik:
│                       │  energy / power / voltage / current /
│                       │  frequency / availability
└──────────┬───────────┘
           │
┌──────────▼───────────┐
│  ENERGY CALCULATION   │  cumulative kWh = source of truth
│  (per Source/Aggregate)│ fallback: Riemann integral dari power W
│                       │  bila cumulative kWh tak tersedia
└──────────┬───────────┘
           │
   ┌───────┴────────┐
   │                │
┌──▼─────────────┐ ┌▼──────────────────┐
│ LIVE PERIOD     │ │ LONG-TERM         │
│ COUNTERS        │ │ STATISTICS        │
│ (utility_meter- │ │ (recorder, tidak  │
│  style, live    │ │  pernah di-purge) │
│  dashboard)     │ │ → sumber histori  │
└──────┬──────────┘ │  hour/day/week/   │
       │             │  month/year       │
       │             └─────────┬─────────┘
       │                       │
┌──────▼───────────────────────▼───────┐
│            COST ENGINE                │  energy_delta × tarif aktif
│  (per Billing Group / Tariff Profile) │  + komponen tambahan + rounding
└──────────┬─────────────────────────────┘
           │
┌──────────▼───────────┐
│    TOKEN ENGINE        │  ledger: total_credited − consumed_since_baseline
│  (per Billing Group)   │  = remaining_kwh; dukung top-up & kalibrasi manual
└──────────┬───────────┘
           │
┌──────────▼───────────┐
│  PREDICTION ENGINE     │  avg harian (24h/7d/30d) → hari tersisa →
│                       │  tanggal habis, dengan confidence level
└──────────┬───────────┘
           │
┌──────────▼───────────┐
│ NOTIFICATION ENGINE    │  state machine NORMAL→WARNING→CRITICAL→
│                       │  VERY_CRITICAL, edge-triggered + cooldown,
│                       │  kirim via Telegram (integrasi terpisah)
└──────────┬───────────┘
           │
┌──────────▼───────────┐
│      DASHBOARD          │  Lovelace: CURRENT / COST / TOKEN / HISTORY
└───────────────────────┘
```

**Implementasi setiap kotak**:
- *Energy Sources* → daftar entity_id apa adanya, tidak diubah oleh sistem ini.
- *Source Normalization* → dihasilkan oleh custom integration sebagai entity platform `sensor`/`binary_sensor` per Energy Source config entry, memakai `DataUpdateCoordinator` yang subscribe ke state perubahan source asli (event-driven, bukan polling, untuk efisiensi) — atau state-trigger template bila Track B.
- *Energy Calculation* → logic Python di dalam integration (Track A) yang memilih cumulative-kWh langsung bila tersedia; kalau hanya ada power, pakai helper `integration` (Riemann sum) sebagai fallback eksplisit, ditandai di entity attribute `source_of_truth: "cumulative"` vs `"integrated_from_power"` supaya user tahu tingkat akurasinya.
- *Live Period Counters* → entity internal integration yang mereplikasi perilaku `utility_meter` (reset di awal tiap cycle: hour/day/week/month/year) tapi terikat ke Billing Group, bukan langsung ke source mentah — supaya cycle bisa berbeda per Billing Group.
- *Long-Term Statistics* → integration mendaftarkan entity kanonik dengan `state_class` yang benar supaya recorder otomatis membuat statistics; histori day/week/month/year dashboard mengambil dari `recorder.statistics_during_period` (via `history`/`statistics` API), bukan re-derive manual.
- *Cost/Token/Prediction/Notification Engine* → modul Python terpisah di dalam integration, masing-masing dijelaskan detail di Bagian F–I.
- *Dashboard* → Lovelace YAML/UI biasa, mengonsumsi entity yang dihasilkan integration — dijelaskan di Bagian J.

---

## D. Data Model

### D.1 Objek konfigurasi (disimpan di config entry / options, Track A)

**EnergySource**
| Field | Tipe | Wajib | Keterangan |
|---|---|---|---|
| `id` | slug | ya | unique, immutable setelah dibuat |
| `name` | string | ya | nama tampilan, mis. "Tongou MCB Lantai 1" |
| `energy_entity_id` | entity_id | tidak* | cumulative kWh — *wajib jika `power_entity_id` kosong* |
| `power_entity_id` | entity_id | tidak* | instantaneous W — dipakai untuk live load & fallback integrasi |
| `voltage_entity_id` | entity_id | tidak | |
| `current_entity_id` | entity_id | tidak | |
| `frequency_entity_id` | entity_id | tidak | |
| `availability_entity_id` | entity_id | tidak | jika kosong, availability diturunkan dari `unavailable`/`unknown` state entity energy/power |
| `enabled` | bool | ya | default true |

**Aggregate**
| Field | Tipe | Keterangan |
|---|---|---|
| `id` | slug | |
| `name` | string | |
| `member_source_ids` | list[id] | boleh berisi EnergySource *atau* Aggregate lain (nested, dengan guard anti-circular) |
| `method` | enum | `sum` (default; satu-satunya method fase awal — jangan asumsikan average/max tanpa diminta) |

**TariffProfile**
| Field | Tipe | Keterangan |
|---|---|---|
| `id`, `name` | | |
| `rate_periods` | list[{start_time, end_time, rate_rp_per_kwh}] | minimal 1 entry (flat 24 jam); mendukung TOU jika suatu saat PLN prepaid Anda pakai skema waktu. **[Diputuskan setelah review user]** Default awal di config flow: 1 entry flat 24 jam, `rate_rp_per_kwh` = Rp 1.444,70 (indikatif golongan R-1 1300–2200VA — **LIKELY**, sumber agregator tarif, lihat B.2, bukan kutipan pln.co.id langsung). Wajib ditampilkan sebagai *starting point*, bukan nilai final — field ini harus punya help text eksplisit yang bilang "sesuaikan dengan golongan daya & tarif PLN Anda saat ini" |
| `fixed_charge_rp` | number, default 0 | lihat catatan di F.3 — pada prepaid residential standar biasanya 0 |
| `fixed_charge_period` | enum: daily/monthly | hanya relevan jika `fixed_charge_rp` > 0 |
| `additional_components` | list[{name, type: flat\|percent, value, applies_to: energy_charge\|subtotal}] | untuk pajak/komponen lain di luar PPJ (PPJ sudah di-handle di Token Engine, lihat G) |
| `rounding` | {mode: nearest\|up\|down, unit_rp: number} | default: round to nearest 1 Rp |
| `effective_from` | datetime | **setiap perubahan tarif membuat versi baru**, bukan overwrite — lihat K.7 |

**TokenAccount** (1 per Billing Group yang mengaktifkan token tracking)
| Field | Tipe | Keterangan |
|---|---|---|
| `id`, `name` | | |
| `linked_measurement_id` | id (Source atau Aggregate) | sumber kWh yang jadi acuan konsumsi |
| `baseline_energy_kwh` | number | snapshot cumulative energy measurement saat tracking dimulai/direset |
| `total_credited_kwh` | number | akumulasi seluruh top-up sejak baseline |
| `topup_history` | list[TokenTopup] | log, tidak pernah dihapus otomatis (untuk audit) |
| `warning_threshold_days` | number, default 7 | |
| `critical_threshold_days` | number, default 3 | |
| `very_critical_threshold_days` | number, default 1 | |
| `warning_threshold_kwh` | number, optional | threshold absolut kWh, opsional selain hari |

**TokenTopup** (entry dalam `topup_history`)
| Field | Tipe | Wajib |
|---|---|---|
| `timestamp` | datetime | ya (default now) |
| `kwh_credited` | number | ya |
| `nominal_rp` | number | tidak (untuk pencatatan saja) |
| `meter_reading_before` / `meter_reading_after` | number | tidak, opsional untuk rekonsiliasi |
| `note` | string | tidak |

**NotificationConfig** (1 per Billing Group)
| Field | Tipe | Keterangan |
|---|---|---|
| `enabled` | bool | |
| `cooldown_hours` | number, default 12 | jarak minimum antar notifikasi berulang untuk level yang sama |
| `repeat_while_unresolved` | bool, default false | jika true, kirim ulang tiap `cooldown_hours` selama status masih ≥ level tsb |
| `quiet_hours_start` / `quiet_hours_end` | time, optional | jam tanpa notifikasi kecuali VERY_CRITICAL |
| `bypass_quiet_hours_for_very_critical` | bool, default true | |
| `telegram_target` | string | target `notify.telegram_*` service atau `chat_id`, lihat catatan K.9 |

**PredictionConfig** (global atau per Billing Group)
| Field | Tipe | Default | Keterangan |
|---|---|---|---|
| `preferred_window` | enum: 24h/7d/30d | 7d | prioritas sesuai permintaan Anda |
| `fallback_order` | list | [7d, 24h, 30d] | urutan fallback bila window preferred data tidak cukup |
| `min_data_points` | number | 3 (hari) untuk window 7d/30d; 6 (jam) untuk 24h | ambang minimal sebelum dianggap "cukup data" |
| `outlier_filter` | enum: none/median/trim_percent | median | metode redam anomali |
| `safety_margin_percent` | number, default 10 | margin konservatif: prediksi pakai `avg_usage × (1 + margin)` supaya tidak overconfident |

### D.2 Entity yang dihasilkan (per Billing Group, prefix `<domain>.<mu_slug>_*`)

| Entity | device_class | state_class | Keterangan |
|---|---|---|---|
| `sensor.<mu>_power` | power | measurement | live load (pass-through/sum) |
| `sensor.<mu>_voltage` | voltage | measurement | |
| `sensor.<mu>_current` | current | measurement | |
| `sensor.<mu>_frequency` | frequency | measurement | |
| `sensor.<mu>_energy_total` | energy | total_increasing | **source of truth** |
| `sensor.<mu>_energy_this_hour/day/week/month/year` | energy | total | live counter, reset tiap awal cycle |
| `sensor.<mu>_cost_total` | monetary | total_increasing | akumulasi Rp berjalan (lihat F.3) |
| `sensor.<mu>_cost_this_hour/day/week/month/year` | monetary | total | |
| `sensor.<mu>_token_remaining_kwh` | energy | measurement | bukan `total_increasing` (nilainya turun) |
| `sensor.<mu>_token_remaining_value_rp` | monetary | measurement | |
| `sensor.<mu>_token_consumed_since_baseline_kwh` | energy | total_increasing | |
| `sensor.<mu>_avg_daily_usage_kwh` | energy | measurement | attribute: `window_used`, `data_points`, `confidence` |
| `sensor.<mu>_estimated_days_remaining` | — | measurement | attribute: `confidence`, `window_used` |
| `sensor.<mu>_estimated_empty_date` | timestamp | — | `unknown` jika data tidak cukup |
| `sensor.<mu>_token_status` | enum | — | `normal`\|`warning`\|`critical`\|`very_critical`\|`unknown` |
| `binary_sensor.<mu>_data_sufficient` | — | — | true/false untuk gate prediksi |
| `binary_sensor.<source>_available` | — | — | 1 per Energy Source, availability upstream |

### D.3 Service (Track A)
- `pln_prepaid_monitor.add_token_topup` — fields: `billing_group_id`, `kwh_credited` (required), `nominal_rp` (optional), `timestamp` (optional, default now), `meter_reading_before/after` (optional), `note` (optional).
- `pln_prepaid_monitor.calibrate_token_reading` — fields: `billing_group_id`, `actual_remaining_kwh` (required), `note` (optional) — untuk sinkronisasi manual terhadap angka yang tertera di layar meter fisik (lihat K.6).
- `pln_prepaid_monitor.edit_topup` / `delete_topup` — koreksi salah input (K.5).
- `pln_prepaid_monitor.reset_token_ledger` — reset penuh baseline (mis. ganti meter fisik, K.4).
- `pln_prepaid_monitor.purge_old_data` — hapus long-term statistics lama milik integration ini sesuai retensi yang dipilih (lihat Bagian N, ditambahkan setelah review user).

---

## E. Configuration Model

Semua parameter berikut wajib dapat diubah dari UI (config flow / options flow), tanpa restart HA, dan tanpa edit file:

1. **Energy Source**: seperti tabel D.1 — tambah/edit/nonaktifkan source kapan pun.
2. **Aggregate**: pilih source mana yang digabung, bukan default "semua dijumlahkan" (prinsip #3 Anda).
3. **Billing Group**: mengikat 1 measurement (source atau aggregate) ke 1 TariffProfile + (opsional) 1 TokenAccount + 1 NotificationConfig. Bisa ada banyak Billing Group, masing-masing independen (`PLN_HOME`, `PLN_SHOP`, dst).
4. **Tarif**: rate Rp/kWh, opsional multi-periode (TOU), fixed charge opsional, additional components list, rounding.
5. **Token**: lihat D.1 TokenAccount + TokenTopup — semua field yang Anda minta di brief sudah tercakup (nominal, kWh aktual, tanggal, source meter, initial reading, threshold).
6. **Prediction**: window prioritas, minimum data, fallback order, safety margin — semua di D.1 PredictionConfig.
7. **Notification**: enable/disable, 3 threshold (warning/critical/very critical) dalam hari **dan** opsional kWh absolut, cooldown, jam notifikasi (quiet hours), target Telegram.

Validasi wajib di config/options flow (bukan hanya di runtime):
- Minimal satu dari `energy_entity_id`/`power_entity_id` terisi per Source.
- `warning_threshold_days > critical_threshold_days > very_critical_threshold_days` (tolak input yang tidak masuk akal, jangan diam-diam "membetulkan").
- Deteksi circular reference pada Aggregate bersarang.
- Entity_id yang dirujuk benar-benar ada di HA saat disimpan (peringatan, bukan hard block, karena entity integrasi lain kadang belum ready saat setup awal).

---

## F. Calculation Logic

### F.1 Energy per periode (hour/day/week/month/year)

Prioritas sumber data (selaras prinsip #4 dan riset B.3):

1. **Utama**: baca `sensor.<mu>_energy_total` (total_increasing, cumulative kWh) → live counter per-Billing-Group yang reset di awal tiap cycle → nilai "this hour/day/..." adalah delta sejak awal cycle.
2. **Histori jangka panjang** (day/week/month/year chart di dashboard): jangan hitung ulang manual — tarik dari recorder long-term statistics (`statistics_during_period`) atas entity `sensor.<mu>_energy_total`, karena data ini tidak pernah di-purge dan sudah dihitung HA secara konsisten.
3. **Fallback bila hanya ada power W** (tidak ada entity energi kumulatif di source): integrasikan power dengan metode Riemann sum (`kWh += (power_W × delta_t_hours) / 1000`) untuk membangun `energy_total` sintetis, dan tandai entity attribute `source_of_truth: "integrated_from_power"` supaya user/dashboard tahu angka ini adalah estimasi (lebih rawan drift dibanding cumulative kWh asli, karena bergantung pada frekuensi sampling power).

### F.2 Penanganan reset counter fisik — **[VERIFIED langsung dari source code HA Core 2026.8.3, diperbarui setelah review]**

Diverifikasi langsung terhadap `homeassistant/components/sensor/recorder.py` pada tag `2026.8.3` (fungsi `reset_detected()` dan `compile_statistics()`), dan dokumentasi resmi developers.home-assistant.io/docs/core/entity/sensor/. Algoritma pastinya:

- Reset **hanya** terpicu kalau nilai baru turun **lebih dari 10%** dari nilai sebelumnya (`fstate < 0.9 × previous_fstate`). Penurunan ≤10% dianggap noise/rounding (dilog sebagai "dip", tidak memicu reset). Nilai negatif dibuang total, tidak pernah diproses sebagai reset atau konsumsi.
- Saat reset genuine terdeteksi: siklus sebelum reset ditutup dulu (konsumsi sampai pembacaan terakhir tetap terhitung penuh, tidak hilang), lalu **titik nol untuk siklus berikutnya diset ke 0 — bukan ke nilai baru yang lebih rendah**. Konsekuensinya: seluruh angka pembacaan pasca-reset langsung terhitung penuh sebagai konsumsi baru ke depan, tanpa ada delta negatif yang dikurangkan dan tanpa kehilangan yang sudah dibanking sebelumnya.
- Sumber kutipan resmi (developer docs, verbatim): *"the logic when updating the statistics is to update the sum column with the difference between the current state and the previous state unless the difference is negative, in which case don't add anything."*
- Perilaku ini stabil sejak diperkenalkan di rilis 2021.9 sampai 2026.8.3 saat ini (tidak ada perubahan algoritma reset/sum, hanya beberapa perbaikan None-safety yang tidak mengubah semantiknya).

**Temuan kritis yang mengoreksi Bagian G.1**: perilaku "selalu naik dan aman terhadap reset" ini adalah properti dari **kolom statistics `sum`** yang dihitung HA di recorder secara berkala (per jam) — **bukan** properti dari raw state entity `total_increasing` itu sendiri. Raw state upstream tetap bisa terbaca turun mendadak persis saat reset terjadi. Artinya formula Token Engine di G.1 yang menghitung `energy_total_now − baseline_energy_kwh` sebagai pengurangan dua snapshot raw state **rentan salah** kalau reset terjadi di antara waktu baseline diambil dan sekarang — hasilnya bisa negatif dan `remaining_kwh` jadi salah membengkak. Lihat perbaikan wajib di G.1 (bagian "Model ledger", sudah diperbarui).

### F.3 Cost Engine

```
cost_increment  = energy_delta_kwh × tariff_rate_active_now(billing_group)
cost_total     += cost_increment          (running Rupiah counter, total_increasing)
```

- `tariff_rate_active_now` mempertimbangkan `rate_periods` (TOU) bila lebih dari satu didefinisikan.
- **Fixed charge & additional components** ditambahkan **hanya di level rollup, bukan dicampur ke `cost_total` per-increment** — karena PLN prepaid residential standar umumnya *tidak* punya biaya beban bulanan terpisah (`fixed_charge_rp` default 0) **[Medium-high confidence, pengetahuan umum konsumen — cek ulang untuk golongan tarif non-residensial/bisnis yang mungkin berbeda]**. Field ini tetap disediakan configurable untuk kasus non-standar, ditambahkan secara prorata di sensor `cost_this_month`/`cost_this_year` saja.
- Karena `cost_total` adalah counter berjalan yang increment-nya sudah pakai tarif yang aktif *saat itu*, perubahan tarif di tengah bulan otomatis tercermin benar di histori (lihat K.7) — tidak perlu recompute retroaktif.
- **Bedakan dua makna "nilai Rupiah"**: (a) `sensor.<mu>_cost_*` = estimasi biaya dari energi yang benar-benar dikonsumsi, dihitung dari tarif dasar per kWh — ini untuk insight pemakaian; (b) `sensor.<mu>_token_remaining_value_rp` = `remaining_kwh × tariff_rate` — estimasi *nilai* sisa token dalam Rupiah, **bukan** perkiraan berapa Rupiah yang perlu dibayar untuk isi ulang sejumlah kWh yang sama (karena isi ulang baru akan kena admin+PPJ lagi, lihat B.2). Dokumentasikan perbedaan ini di dashboard supaya tidak menyesatkan user.
- Rounding diterapkan di titik presentasi (entity display), bukan mengubah `cost_total` mentah, supaya tidak ada akumulasi galat pembulatan pada counter jangka panjang.

---

## G. Token Logic

### G.1 Model ledger (bukan snapshot-per-topup) — **[Diperbaiki setelah verifikasi F.2]**

```
remaining_kwh = total_credited_kwh − consumed_since_baseline_kwh
```

**Koreksi penting**: `consumed_since_baseline_kwh` **wajib diakumulasi incremental, per setiap perubahan state pada source/aggregate terkait** — TIDAK BOLEH dihitung sebagai pengurangan dua snapshot raw state (`energy_total_now − baseline_energy_kwh`) seperti draft sebelumnya. Alasannya: raw state upstream `total_increasing` bisa turun mendadak saat reset counter fisik (lihat F.2), dan pengurangan titik-ke-titik akan menghasilkan angka negatif/salah kalau reset terjadi di antara dua pembacaan itu.

Cara yang benar — pakai **algoritma reset-safe yang sama persis dengan yang dipakai HA core sendiri** (VERIFIED di F.2: threshold dip 10%, titik-nol-direset-ke-0 saat reset genuine), dijalankan sebagai listener incremental setiap kali source berubah state (bukan dihitung ulang dari dua snapshot):

```
on setiap state-change source (raw_new):
    if raw_prev is None:
        raw_prev = raw_new                      # inisialisasi, tidak ada delta pertama kali
    elif raw_new < 0:
        log warning, abaikan reading ini          # sama seperti HA core
    elif raw_new < 0.9 × raw_prev:
        # reset genuine terdeteksi
        consumed_since_baseline_kwh += (raw_prev - zero_point)   # tutup siklus lama
        zero_point = 0                                            # bukan raw_new
        raw_prev = raw_new
    else:
        # kenaikan normal, atau dip ≤10% (noise, tetap diproses apa adanya)
        consumed_since_baseline_kwh += (raw_new - raw_prev)
        raw_prev = raw_new
```

Ini pada dasarnya adalah "Live Period Counter" (Bagian C) yang sudah ada di arsitektur, hanya di-scope khusus untuk periode "sejak baseline token" alih-alih per jam/hari/dll — jadi tidak perlu komponen baru, cukup instance tambahan dari mekanisme delta-accumulator yang sama.

- `baseline_energy_kwh`/`zero_point` diset sekali saat tracking dimulai (topup pertama) atau saat `reset_token_ledger`/`calibrate_token_reading` dipanggil — titik itu jadi awal akumulasi `consumed_since_baseline_kwh` (mulai dari 0).
- Setiap `add_token_topup` menambah `total_credited_kwh += kwh_credited` — **tidak mengganti/reset** nilai sebelumnya.
- `meter_reading_before/after` di setiap topup bersifat opsional, dipakai hanya untuk audit/rekonsiliasi, bukan input wajib untuk kalkulasi.
- **Wajib persist `consumed_since_baseline_kwh`, `raw_prev`, dan `zero_point` lintas restart HA** (bukan cuma `baseline_energy_kwh` awal) — kalau tidak, restart di tengah-tengah bisa kehilangan state akumulasi incremental ini. Simpan di config entry runtime data yang di-restore saat integration reload, bukan dihitung ulang dari nol.

### G.2 Asumsi kunci — **[CONFIRMED oleh user, 2 Sept 2026]**

Model di atas mengasumsikan meter fisik PLN bersifat *additive* — kalau Anda top-up token baru sebelum token lama habis, sisa lama **ditambahkan** ke token baru (bukan ditimpa). **Ini sudah dikonfirmasi langsung oleh user lewat pengecekan fisik ke layar meter**: top-up baru menambahkan kWh ke sisa lama, bukan mereset ke nilai baru saja. Ledger model di G.1 (additive: `total_credited_kwh += kwh_credited`, tidak pernah overwrite) adalah desain yang tepat untuk kasus ini — **tidak diperlukan mode "replace" alternatif**. `calibrate_token_reading` (G.3) tetap dipertahankan di arsitektur, tapi perannya sekarang murni sebagai alat koreksi drift (selisih akurasi metering source vs meter resmi PLN, atau setelah insiden di Bagian K), bukan lagi mitigasi untuk asumsi additive yang mungkin salah.

### G.3 Kalibrasi manual (drift correction)

`calibrate_token_reading(actual_remaining_kwh)`:
```
baseline_energy_kwh = energy_total_now
total_credited_kwh  = actual_remaining_kwh
```
Ini mereset ledger match dengan angka aktual yang dibaca langsung dari layar meter — dipakai untuk mengoreksi drift akibat (a) asumsi additive yang salah, (b) selisih akurasi metering source vs meter resmi PLN, atau (c) setelah insiden di Bagian K.

---

## H. Prediction Logic

```
avg_daily_usage_kwh = pilih_window(preferred_window, fallback_order, min_data_points)
                       dari long-term statistics sensor.<mu>_energy_total,
                       dengan outlier_filter diterapkan (median/trim)

IF avg_daily_usage_kwh tidak tersedia (data < min_data_points):
    estimated_days_remaining = unknown
    estimated_empty_date     = unknown
    confidence               = "insufficient_data"
ELSE IF avg_daily_usage_kwh <= epsilon (mendekati nol):
    estimated_days_remaining = unknown   # hindari division-by-zero / infinity palsu
    confidence               = "insufficient_usage"
ELSE:
    adjusted_avg = avg_daily_usage_kwh × (1 + safety_margin_percent/100)
    estimated_days_remaining = remaining_kwh / adjusted_avg
    estimated_empty_date     = now + estimated_days_remaining
    confidence               = "high" jika window=7d & data_points >= 7
                                "medium" jika window=24h atau data_points 3-6
                                "low" jika window=30d dengan data_points < 30
```

- **Window selection**: coba `preferred_window` (default 7d) dulu; kalau data historis Billing Group ini belum cukup (baru dipasang < 7 hari), turun ke `fallback_order` berikutnya (24h), dengan `confidence` diturunkan sesuai.
- **Anomaly handling**: `outlier_filter: median` memakai median harian alih-alih mean supaya satu hari dengan lonjakan konsumsi ekstrem (tamu menginap, AC nyala terus) tidak mendistorsi prediksi secara tidak proporsional.
- **Jangan pernah menampilkan angka prediksi presisi saat data belum cukup** — tampilkan state `unknown`/"data belum cukup" di dashboard, bukan menebak dengan default value tersembunyi. Ini eksplisit dari permintaan Anda dan prinsip transparansi.
- Semua output prediksi membawa attribute `confidence` dan `window_used` agar dashboard bisa menampilkannya (mis. label kecil "berdasarkan rata-rata 7 hari, keyakinan tinggi").

---

## I. Notification Logic

### I.1 State machine (per Billing Group)

```
determine_level(estimated_days_remaining, remaining_kwh, config):
    IF estimated_days_remaining == unknown: return "unknown"
    IF estimated_days_remaining <= very_critical_threshold_days
       OR (kwh threshold aktif AND remaining_kwh <= warning_threshold_kwh_very_critical): return "very_critical"
    IF estimated_days_remaining <= critical_threshold_days: return "critical"
    IF estimated_days_remaining <= warning_threshold_days: return "warning"
    RETURN "normal"
```

### I.2 Pengiriman notifikasi (anti-spam)

- **Edge-triggered**: kirim notifikasi Telegram hanya saat `token_status` **berubah level** (mis. normal→warning, warning→critical), dicatat via `last_notified_level` per Billing Group.
- **Cooldown backstop**: jika `repeat_while_unresolved = true`, boleh kirim ulang di level yang sama, tapi tidak lebih sering dari `cooldown_hours`.
- **Quiet hours**: notifikasi ditahan di `quiet_hours_start`–`quiet_hours_end`, **kecuali** `very_critical` dan `bypass_quiet_hours_for_very_critical = true` — supaya darurat tetap sampai.
- **Reset otomatis ke normal**: begitu `add_token_topup` atau `calibrate_token_reading` membuat `token_status` kembali ke `normal`, kirim satu notifikasi konfirmasi "token sudah terisi ulang" (opsional, configurable), dan reset `last_notified_level`.
- Channel Telegram **bukan dibangun ulang oleh sistem ini** — integrasi ini memanggil service `notify.*` yang sudah dikonfigurasi lewat integrasi resmi `telegram_bot` HA (prasyarat eksternal, di luar scope spec ini — pastikan itu sudah terpasang dan diuji terpisah sebelum mengandalkan notifikasi dari sistem ini).

---

## J. Dashboard Design

Layout Lovelace, 4 seksi sesuai permintaan Anda. Deskripsi struktural (bukan YAML implementasi):

**Header** — pemilih Billing Group aktif (jika lebih dari satu), status chip besar (`token_status` dengan warna: hijau/kuning/oranye/merah).

**CURRENT** (grid kartu, per Billing Group + optional breakdown per Energy Source anggota)
- Power (gauge/mushroom entity card)
- Voltage, Current (compact glance)
- Energy total (angka besar + trend sparkline harian)

**COST** (row kartu angka: Hour / Today / Week / Month / Year), masing-masing memakai entity `sensor.<mu>_cost_*`, dengan grafik batang bulan berjalan.

**TOKEN** (kartu ringkasan)
- Remaining kWh (angka besar)
- Remaining value (Rp)
- Average daily usage (dengan badge confidence)
- Estimated days remaining
- Estimated empty date
- Tombol/shortcut ke form `add_token_topup` dan `calibrate_token_reading`

**MAINTENANCE** (kartu kecil terpisah, opsional ditaruh di sub-halaman/expander agar tidak mengganggu tampilan utama)
- Tombol "Hapus data lama" yang memanggil service `purge_old_data` (Bagian N), **wajib pakai dialog konfirmasi** karena aksinya permanen — tampilkan jelas periode retensi yang akan dihapus sebelum eksekusi.

**HISTORY** (dua grafik: `statistics-graph` untuk energi harian & bulanan, dan untuk cost harian & bulanan), memakai card bawaan HA `statistics-graph`/`history-graph` yang membaca langsung dari long-term statistics.

**Catatan pemilihan card**: Mushroom Cards boleh dipakai untuk tampilan ringkas/estetik (gauge, entity, template card) **karena bersifat opsional kosmetik**, tapi setiap card kritikal (grafik histori, tombol layanan) harus punya fallback dengan card bawaan HA (`entities`, `glance`, `statistics-graph`, `gauge`) supaya dashboard tetap berfungsi penuh tanpa HACS/Mushroom terpasang — sesuai prinsip "jangan membuat dependency yang tidak diperlukan".

---

## K. Failure Scenarios

| Skenario | Deteksi | Perilaku sistem | Pemulihan |
|---|---|---|---|
| **1. Source unavailable sesaat** (< beberapa menit, mis. WiFi drop) | `availability_entity`/state entity jadi `unavailable` | Hold nilai terakhir untuk `power` (jangan drop ke 0 — itu akan salah dianggap "tidak ada beban"); `energy_total` tetap diam (tidak infer delta apa pun) | Otomatis resume begitu source kembali `available` |
| **2. Source unavailable lama** (jam/hari) | Durasi unavailable > threshold configurable | `binary_sensor.<source>_available = false`, prediction engine tandai `confidence` turun karena ada gap data, dashboard tampilkan warning visual eksplisit | Manual/otomatis begitu reconnect; histori tidak diisi mundur (gap tetap tercatat sebagai gap, tidak diinterpolasi diam-diam) |
| **3. HA restart/reboot** | — | Config entry (Track A) atau helper (Track B) me-restore semua state konfigurasi otomatis (VERIFIED untuk `input_number`/`input_datetime`); `energy_total` melanjutkan dari state terakhir HA (bukan dari 0) karena `total_increasing` + recorder restore | Tidak perlu intervensi user |
| **4. Meter/source diganti fisik** (bukan reset counter biasa, tapi ganti device baru dengan reading dari 0) | Manual — user memanggil `reset_token_ledger` | `baseline_energy_kwh` diset ulang ke reading device baru, histori lama tetap tersimpan di statistics tapi ledger token mulai dari titik baru | User wajib memicu ini secara sadar; sistem tidak menebak kapan ini terjadi otomatis |
| **5. Sensor reset counter firmware** (drop mendadak lalu naik lagi dari rendah) | `total_increasing` semantics menangani ini native (lihat F.2) | Tidak dihitung sebagai konsumsi negatif; akumulasi lanjut dari nilai baru | Otomatis, tapi disarankan alert low-severity ke log HA untuk audit (bukan notifikasi Telegram, supaya tidak spam) |
| **6. Integration failure/reload** (mis. Tuya cloud down) | Sama seperti skenario 1/2 pada level source | Sama seperti skenario 1/2, di-scope ke source yang terdampak; Billing Group dengan >1 source (aggregate) tetap parsial berfungsi untuk source lain yang sehat | Otomatis begitu integrasi pulih |
| **7. Wrong token input** (salah ketik kWh/nominal) | Manual, ditemukan user | Service `edit_topup`/`delete_topup` mengoreksi entry tertentu di `topup_history` dan sistem recompute `total_credited_kwh` dari seluruh histori (bukan dari cache tunggal) | Immediate setelah koreksi |
| **8. Tariff berubah di tengah bulan** | User update TariffProfile | `cost_total` counter tidak direcompute retroaktif — increment lama sudah terhitung dengan tarif lama (benar secara akuntansi); tarif baru berlaku untuk increment berikutnya | Otomatis, tidak perlu aksi lain — versi tarif lama tetap di `effective_from` histori untuk audit |
| **9. Division by zero / avg usage = 0** | Prediction engine guard (H) | Return `unknown`, bukan `infinity`/error | Otomatis pulih begitu ada data konsumsi baru |
| **10. Data belum cukup (baru install)** | `data_points < min_data_points` | `binary_sensor.<mu>_data_sufficient = false`; semua sensor prediksi `unknown`; dashboard tampilkan pesan eksplisit "mengumpulkan data" | Otomatis setelah cukup hari berjalan |
| **11. Dua Billing Group tanpa sengaja memakai source yang sama** (double counting) | Validasi config flow (soft warning, bukan hard block — bisa jadi disengaja untuk 2 sudut pandang berbeda) | Sistem tetap jalan (user mungkin sengaja), tapi beri peringatan eksplisit di UI options flow saat overlap terdeteksi | User memutuskan sadar |
| **12. Konsumsi anomali mendadak** (lonjakan tak wajar) | Deviasi > threshold dari median historis (opsional, configurable) | Prediksi tetap dihitung tapi `outlier_filter` meredam pengaruhnya; opsional flag `binary_sensor.<mu>_anomaly_detected` untuk visibilitas, **bukan** untuk trigger notifikasi otomatis (di luar scope notifikasi token) | Manual review user |

---

## L. Implementation Specification (untuk Claude Code)

### L.1 Bentuk deliverable
Custom integration Home Assistant, struktur standar `custom_components/pln_prepaid_monitor/`:
```
custom_components/pln_prepaid_monitor/
├── __init__.py                 # setup entry, forward ke platforms
├── manifest.json                # domain, requirements, codeowners, iot_class: local_push/local_polling sesuai source
├── config_flow.py               # ConfigFlow + OptionsFlow: Source/Aggregate/Billing Group/Tariff/Token/Notification
├── const.py
├── coordinator.py               # DataUpdateCoordinator per Billing Group (atau shared, dengan sub-listener per source)
├── sensor.py                    # semua entity di D.2 kategori sensor
├── binary_sensor.py             # availability, data_sufficient
├── services.py + services.yaml  # add_token_topup, calibrate_token_reading, edit_topup, delete_topup, reset_token_ledger
├── engines/
│   ├── normalization.py
│   ├── energy_calc.py           # cumulative-first, Riemann fallback
│   ├── cost_engine.py
│   ├── token_engine.py
│   ├── prediction_engine.py
│   └── notification_engine.py
├── statistics_helper.py         # wrapper resmi ke recorder statistics API
├── strings.json / translations/en.json, id.json
└── tests/                       # pytest + pytest-homeassistant-custom-component
```

### L.2 Prinsip koding wajib
- Semua I/O async, tidak ada blocking call di event loop (pola standar HA core).
- Setiap entity punya `unique_id` stabil (berbasis config entry id + slug), supaya user bisa rename/kustomisasi dari UI tanpa kehilangan histori statistics.
- Device registry: group entity per Energy Source sebagai satu `device`, dan per Billing Group sebagai `device` virtual terpisah — supaya dashboard auto-generate HA (device page) tetap berguna.
- Jangan pernah mendaftarkan platform `switch`/memanggil service kontrol daya — audit ini eksplisit di code review sebelum rilis (non-negotiable, lihat Executive Summary).
- Statistics historis dibaca lewat `recorder.statistics_during_period`/`recorder.get_instance(hass).async_add_executor_job(...)` resmi, bukan query SQL manual ke DB recorder.
- Gunakan `state_class`/`device_class` persis sesuai tabel D.2 supaya recorder membuat long-term statistics secara otomatis dan kompatibel dengan Energy Dashboard bawaan HA (energy sensor bisa didaftarkan juga ke Energy Dashboard native sebagai bonus, opsional).
- Semua string user-facing lewat `strings.json`/translations (minimal `en` + `id`).
- **[Wajib, permintaan user]** Setiap field di config flow/options flow (Source, Aggregate, Billing Group, Tariff, Token, Notification, Retention) disertai `description`/help text yang rinci dan ditulis untuk orang awam — bukan cuma nama field teknis. Contoh: field `rate_rp_per_kwh` bukan cuma label "Tariff (Rp/kWh)" tapi disertai penjelasan singkat "Tarif dasar listrik per kWh sesuai golongan daya PLN Anda, bisa dilihat di aplikasi PLN Mobile atau struk token terakhir". Ini berlaku juga untuk atribut entity dan deskripsi service di `services.yaml`.
- **[Wajib, permintaan user]** Sertakan `README.md` lengkap di root repo: langkah instalasi (manual copy vs HACS), cara menambah Energy Source pertama kali, cara memasukkan token pertama kali, cara membaca dashboard, dan troubleshooting dasar (entity `unavailable`, prediksi `unknown`, dll) — ditulis bertahap dan dijelaskan seolah pembaca belum pernah pakai custom integration sebelumnya.

### L.3 Urutan implementasi yang disarankan (milestone)
1. **Source Normalization + Config Flow** — bisa ditest berdiri sendiri: tambah 1 source, lihat entity kanonik muncul dan availability benar.
2. **Energy Calculation + live period counters + statistics registration** — verifikasi long-term statistics benar-benar terbentuk di recorder (test dengan `freezer`/time-travel di pytest).
3. **Cost Engine** — dengan TariffProfile sederhana (flat rate) dulu, baru tambahkan TOU/additional components.
4. **Token Engine + services** (`add_token_topup`, `calibrate_token_reading`, `edit_topup`, `delete_topup`, `reset_token_ledger`) — unit test ledger math secara terpisah dari HA runtime (pure function).
5. **Prediction Engine** — unit test semua guard (division-by-zero, insufficient data, outlier).
6. **Notification Engine** — test state machine transitions + cooldown + quiet hours secara terisolasi, baru integrasikan dengan `telegram_bot` service call di lapisan paling luar.
7. **Dashboard** — deliverable terpisah (Lovelace config/blueprint), dibuat setelah semua entity di atas stabil.
8. **Data Retention Engine** (lihat Bagian N, sudah diverifikasi ke source recorder di N.4) — tetap dikerjakan paling akhir karena implementasinya paling rapuh (bergantung ORM internal recorder, bukan API publik) dan paling butuh test menyeluruh sebelum dipercaya di data produksi.

### L.4 Testing minimum yang harus ada
- Ledger token: multi-topup stacking, kalibrasi manual, edit/delete entry — assert `total_credited_kwh` dan `remaining_kwh` benar di setiap kasus.
- Reset counter fisik (F.2): simulasikan source value turun lalu naik, assert `energy_total` tidak mencatat konsumsi negatif.
- Prediksi: assert `unknown` (bukan crash/infinity) saat `avg_daily_usage = 0` dan saat `data_points < min_data_points`.
- Notifikasi: assert tidak ada duplicate send dalam cooldown window; assert very_critical menembus quiet hours saat bypass=true.
- Restart persistence: simulasikan HA restart (reload config entry) dan assert semua nilai (source config, token ledger, tariff) tetap sama.
- Purge/retensi (Bagian N): assert purge hanya menghapus statistics milik entity buatan integration ini, tidak pernah menyentuh entity/domain lain di recorder milik user.

---

## N. Data Retention & Maintenance (ditambahkan setelah review user)

Fitur baru — user secara eksplisit meminta kemampuan menghapus data lama untuk menjaga ukuran database recorder, karena long-term statistics tidak pernah di-purge otomatis (lihat B.3/M.4 lama).

### N.1 Konfigurasi
| Field | Tipe | Default | Keterangan |
|---|---|---|---|
| `statistics_retention_years` | enum: 1 / 2 / 3 / 5 / unlimited | `unlimited` | retensi long-term statistics **khusus milik integration ini** |
| `auto_purge_enabled` | bool | false | jika true, purge otomatis dijalankan berkala (mis. bulanan) sesuai `statistics_retention_years` |

### N.2 Service
- `pln_prepaid_monitor.purge_old_data` — fields: `billing_group_id` atau `all` (opsional, default semua Billing Group milik integration ini), `keep_years` (opsional, override sekali pakai dari config). Dipanggil manual (tombol di dashboard, dengan dialog konfirmasi karena bersifat **permanen/tidak bisa di-undo**) atau otomatis jika `auto_purge_enabled = true`.

### N.3 Batasan keamanan (non-negotiable)
Purge **hanya boleh menghapus statistics/state milik entity yang dibuat integration ini** (di-scope by `unique_id`/domain integration) — tidak boleh menyentuh entity, domain, atau data recorder lain milik user. Ini harus divalidasi lewat test eksplisit (L.4).

### N.4 Catatan implementasi — **[VERIFIED langsung dari source code HA Core 2026.8.3]**

Sudah diverifikasi tuntas terhadap source `homeassistant/components/recorder/` pada tag `2026.8.3`. Kesimpulan: **tidak ada API resmi HA yang bisa menghapus long-term statistics lama secara selektif per rentang waktu untuk entity tertentu.** Ini bukan celah riset — memang tidak tersedia di HA saat ini:

- `recorder.purge_entities` (service bawaan) — **hanya membersihkan tabel `states`/events, sama sekali tidak menyentuh tabel `statistics`/`statistics_short_term`** (dikonfirmasi dari `purge.py`, fungsi `purge_entity_data`). Tidak berguna untuk kebutuhan kita.
- `recorder/clear_statistics` (websocket command yang dipakai fitur delete di Developer Tools → Statistics, dan fungsi Python `Recorder.async_clear_statistics`/`statistics.clear_statistics` yang membungkusnya) — **all-or-nothing, tanpa parameter rentang waktu**, dan secara teknis menghapus baris `StatisticsMeta` yang men-cascade (foreign key `ON DELETE CASCADE`) menghapus SEMUA baris `statistics`+`statistics_short_term` milik entity itu — termasuk histori yang seharusnya masih disimpan. **Tidak cocok** untuk "hapus yang lebih tua dari N tahun, sisanya tetap".

**Jalan yang tersedia — satu-satunya, dan ini bukan API publik resmi, melainkan mengandalkan internal recorder**: integration kita menulis DELETE langsung lewat ORM (model `Statistics`/`StatisticsShortTerm` di `homeassistant.components.recorder.db_schema`, **bukan raw SQL string**) yang dijalankan di thread recorder sendiri lewat `recorder.get_instance(hass).async_add_executor_job(...)` (wajib — `Recorder.async_clear_statistics` punya guard eksplisit "not thread-safe, must be called from the recorder thread"), difilter ke `StatisticsMeta.statistic_id IN (entity milik kita)` dan `start_ts < cutoff`, dengan **baris `StatisticsMeta` itu sendiri sengaja TIDAK ikut dihapus** (supaya tidak memicu cascade yang menghapus semua histori + supaya cache LRU metadata milik recorder tidak korup). Delete wajib di-batch (hormati batas `max_bind_vars` yang dipakai core sendiri) supaya tidak memegang write-lock lama yang bentrok dengan proses kompaksi hourly recorder.

**Risiko yang harus didokumentasikan jujur ke user, bukan disembunyikan**: pendekatan ini menyentuh struktur internal recorder (ORM model, task queue thread) yang **bukan API publik stabil** — beda dari bagian lain sistem ini yang semuanya memakai API resmi HA. Artinya fitur purge ini punya risiko kompatibilitas lebih tinggi terhadap upgrade major HA Core di masa depan dibanding fitur lain. Mitigasi yang disarankan: bungkus pemanggilan ini dengan try/except yang eksplisit, deteksi kalau struktur tabel berubah (mis. import gagal), dan gagal dengan pesan jelas ke user ("fitur purge tidak kompatibel dengan versi HA ini, silakan hapus manual lewat Developer Tools") alih-alih diam-diam gagal atau — lebih buruk — salah menghapus data.

---

## M. Open Risks, Assumptions & Verification Checklist — status setelah review user (2 Sept 2026)

1. ✅ **[CONFIRMED oleh user]** Meter PLN additive saat top-up — dikonfirmasi lewat cek fisik ke layar meter. Lihat G.2 (sudah diperbarui, tidak lagi berstatus asumsi terbuka).
2. ✅ **[Confidence naik, tetap perlu sanity check teknis]** User menjalankan Core 2026.8.3 (lihat B.4), yaitu setelah linimasa removal legacy template (2026.6) yang disebut riset — jadi kemungkinan besar sintaks lama memang sudah tidak berlaku di instance ini. Linimasa itu sendiri tetap LIKELY (bukan dari changelog resmi yang saya baca langsung), jadi Claude Code tetap wajib melakukan satu sanity check langsung sebelum menulis kode template (lihat B.4).
3. ✅ **[RESOLVED — diverifikasi langsung ke source code HA Core 2026.8.3]** Perilaku persis `total_increasing` terhadap reset counter (F.2) sudah VERIFIED lengkap dengan algoritma, threshold 10%, dan kutipan kode/dokumentasi resmi. Verifikasi ini juga **menemukan dan memperbaiki bug desain nyata** di Token Engine G.1 (formula lama rentan salah kalau reset terjadi di antara dua snapshot raw state) — sudah diperbaiki jadi akumulasi incremental reset-safe. Unit test eksplisit di L.4 tetap wajib, tapi sekarang untuk memverifikasi implementasi Claude Code sudah benar sesuai algoritma yang sudah pasti ini, bukan lagi untuk menebak perilakunya.
4. ✅ **[Ditindaklanjuti sebagai fitur, dan diverifikasi]** User meminta fungsi reset/purge data historis dengan pilihan retensi — didesain sebagai fitur penuh di **Bagian N**. Bagian implementasinya (N.4) sudah diverifikasi langsung ke source: **tidak ada API resmi HA untuk ini**, jalan satu-satunya adalah delete langsung lewat ORM recorder yang dijalankan di thread recorder sendiri, dengan risiko kompatibilitas jangka panjang yang sudah didokumentasikan jujur di N.4 — bukan lagi celah riset, tapi keputusan desain yang sadar akan trade-off-nya.
5. ✅ **[Terpenuhi]** Integrasi `telegram_bot` sudah terpasang & berfungsi di instance user — tidak perlu instruksi setup Telegram terpisah lagi. Claude Code tinggal meminta user memilih target `notify.*` yang sesuai saat konfigurasi `telegram_target` (D.1 NotificationConfig).
6. ✅ **[Diputuskan: Track A, dengan syarat tambahan]** Custom integration tetap fully configurable, **dengan syarat setiap elemen UI (config flow, options flow, service, entity, dashboard) disertai penjelasan rinci berbahasa awam** — sudah ditambahkan sebagai requirement wajib di L.2 (help text detail) dan L.1 (README lengkap step-by-step).
7. ✅ **[Diputuskan]** Tarif contoh di B.2 dipakai sebagai **default awal** di form config flow, bukan hardcoded logic — lihat D.1 TariffProfile (diperbarui) dan pastikan help text field ini menjelaskan bahwa angka tersebut hanya perkiraan (LIKELY confidence, sumber agregator, bukan pln.co.id langsung) dan wajib diverifikasi/diubah user sesuai golongan daya & wilayah aktual.

---

---

## O. Lampiran: Inventaris Entity Riil (ditarik dari HA milik user via `/api/states`, 3 Sept 2026)

Ini bukan lagi riset umum — ini **[VERIFIED, ground truth langsung dari instance HA user]**, ditarik lewat REST API (`GET /api/states`) dari sistem yang sebenarnya, 1.521 entity total, difilter ke yang relevan energi/listrik. Bagian ini menggantikan asumsi generik di B.1/B.3 dengan data nyata — Claude Code harus memakai entity_id ini sebagai contoh konkret saat membangun & menguji config flow, bukan menebak.

### O.1 Koreksi penting terhadap Bagian B.1

**MCB TONGOU milik user terintegrasi lewat Zigbee** (entity_id berprefiks `0x385b44fffed7fa8d` — format IEEE address Zigbee, plus entity `sensor.*_linkquality` yang khas Zigbee2MQTT/ZHA), **bukan** via Tuya cloud/WiFi seperti yang diasumsikan riset generik di B.1 (yang menandai varian Zigbee sebagai "tidak ditemukan bukti"). Ini kabar baik: berarti tidak ada isu cloud-dependency/local-key Tuya sama sekali untuk device ini — jalur integrasinya sudah lokal penuh lewat Zigbee. **Update confidence B.1 dari UNKNOWN → VERIFIED (Zigbee, spesifik untuk unit milik user ini)**; catatan generik soal Tuya di B.1 tetap relevan untuk model TO-Q-SYS-JWT lain yang dijual dengan firmware WiFi, hanya tidak berlaku untuk instalasi user ini.

### O.1b Ringkasan protokol per device (dikonfirmasi user: campuran Zigbee & Tuya, merek Tongou & Tomzn)

User mengonfirmasi mereka punya beberapa MCB dengan protokol dan merek berbeda-beda. Saya re-analisis ulang `entities.json` yang sama (tanpa perlu tarik data baru — lihat O.1c) dengan metode **brand-agnostic**: mengelompokkan entity berdasarkan kombinasi `device_class` (power/current/voltage/energy/frequency) yang muncul bersamaan per device, plus mendeteksi sensor `_linkquality` (penanda diagnostik khas Zigbee2MQTT/ZHA) sebagai sinyal protokol.

| Device | Sinyal Zigbee (`_linkquality`) | IEEE-address-style entity_id | Kesimpulan protokol |
|---|---|---|---|
| MCB TONGOU (`0x385b44fffed7fa8d`) | Ada | Ada | **Zigbee** — [VERIFIED] |
| COLOKAN HA | Ada | — | **Zigbee** — [VERIFIED] |
| COLOKAN FREEZER ARTUGO | Ada | — | **Zigbee** — [VERIFIED] |
| COLOKAN SHOWCASE BESAR | **Tidak ada** | — | Bukan Zigbee — kemungkinan Tuya WiFi atau protokol lain — **[LIKELY, tidak bisa dipastikan 100% dari `/api/states` saja]** |
| MCB RUMAH | Tidak ada | — | Bukan Zigbee — merek tidak disebut di entity_id, kemungkinan Tuya WiFi (mis. Tomzn) atau Modbus — **[UNKNOWN, perlu Anda konfirmasi merek+protokolnya kalau mau didokumentasikan pasti]** |
| MCB TOKO | Tidak ada | — | Sama seperti MCB RUMAH — **[UNKNOWN]** |
| TOB9S-VAP TOMZN (input+output) | Tidak ada | — | Nama produk cocok dengan lini energy meter WiFi/Tuya merek **Tomzn** (model TOB9S-VAP) — **[LIKELY, berdasarkan nama model, bukan konfirmasi eksplisit dari data]** |
| Battery1 (BMS) | Tidak ada | — | Kemungkinan Modbus/RS485 BMS, bukan Zigbee/Tuya — **[UNKNOWN]** |

**Catatan penting soal keterbatasan metode ini**: endpoint `/api/states` yang saya baca **tidak** menyertakan info integrasi/platform HA per entity (tidak ada field "integration: tuya" dsb.) — kesimpulan protokol di atas murni inferensi dari pola entity_id dan ada/tidaknya sensor `_linkquality`. Kalau Anda ingin kepastian 100% per device (mis. untuk MCB RUMAH/TOKO), cara pastinya: Settings → Devices & Services → klik device tersebut → nama integrasi tertera di halaman info device. **Ini tidak menghambat implementasi** — desain generic di seluruh dokumen ini sengaja tidak peduli protokol backend apa pun (Zigbee/Tuya/Modbus/ESPHome semua diperlakukan sama lewat Source Normalization), jadi ketidakpastian ini murni informasi tambahan, bukan blocker.

### O.1c Apakah perlu pull data baru?

**Tidak perlu.** Satu file `entities.json` yang sudah ditarik cukup — itu dump lengkap seluruh 1.521 entity di HA Anda dalam satu waktu, dan analisis ulang di atas (pengelompokan brand-agnostic by device_class) sudah menyisir ulang seluruh isinya, bukan cuma keyword yang saya pakai pertama kali. Hasilnya konsisten dengan yang Anda sampaikan: campuran Zigbee (Tongou, 2 colokan) dan kemungkinan Tuya/lainnya (MCB RUMAH, MCB TOKO, TOB9S-VAP TOMZN, COLOKAN SHOWCASE BESAR). Tidak ditemukan device energy-meter lain di luar yang sudah tercatat di O.2. Login ulang hanya perlu diulang kalau Anda menambah/mengganti perangkat setelah tanggal dokumen ini (3 Sept 2026).

### O.2 Kandidat Energy Source riil yang sudah ada di HA user

| Nama & entity prefix | energy (kWh, total_increasing) | power | voltage | current | frequency | Catatan |
|---|---|---|---|---|---|---|
| **MCB TONGOU** (`0x385b44fffed7fa8d`) | `sensor.0x385b44fffed7fa8d_energy` (21.507,97) | `sensor.0x385b44fffed7fa8d_power` (W) | `sensor.0x385b44fffed7fa8d_voltage` (V) | `sensor.0x385b44fffed7fa8d_current` (A) | — | Zigbee, VERIFIED. Juga ada `sensor.0x385b44fffed7fa8d_temperature` (bukan bagian skema kanonik kita) |
| **MCB RUMAH** | `sensor.mcb_rumah_total_energy` (15.498,27) | `sensor.mcb_rumah_phase_a_power` (**satuan kW**, bukan W) | `sensor.mcb_rumah_phase_a_voltage` | `sensor.mcb_rumah_phase_a_current` | `sensor.mcb_rumah_supply_frequency` | Naming "Phase A" mengindikasikan meter ini punya kapasitas multi-fasa tapi cuma fasa A yang dipakai/terpasang — cukup map fasa A saja sebagai source single-phase |
| **MCB TOKO** | `sensor.mcb_toko_total_energy` (15.114,43) | `sensor.mcb_toko_phase_a_power` (W) | `sensor.mcb_toko_phase_a_voltage` | `sensor.mcb_toko_phase_a_current` | `sensor.mcb_toko_supply_frequency` | Struktur sama dengan MCB RUMAH |
| **COLOKAN HA** (smart plug) | `sensor.colokan_ha_energy` | `sensor.colokan_ha_power` | `sensor.colokan_ha_voltage` | `sensor.colokan_ha_current` | — | Sub-meter per perangkat — contoh nyata "sub-meter" dari prinsip #3 Anda |
| **COLOKAN FREEZER ARTUGO** | `sensor.colokan_freezer_artugo_energy` | `..._power` | `..._voltage` | `..._current` | — | idem |
| **COLOKAN SHOWCASE BESAR** | `sensor.colokan_showcase_besar_energy` | `..._power` | `..._voltage` | `..._current` | — | idem |
| **Solar Inverter TOB9S-VAP TOMZN — OUTPUT** | `sensor.tob9s_vap_tomzn_total_energy` | `..._power` | `..._voltage` | `..._current` | — | Contoh nyata "Solar inverter" dari prinsip #1 Anda |
| **Solar Inverter TOB9S-VAP TOMZN — INPUT** | `sensor.input_inverter_tob9s_vap_tomzn_total_energy` | `..._power` | `..._voltage` | `..._current` | — | Energi masuk vs keluar inverter — dua source terpisah |
| **Battery1 (BMS)** | `sensor.battery1_total_energy_charge_meter` / `..._discharge_meter` (keduanya `total_increasing`) | `sensor.battery1_power` | `sensor.battery1_voltage` | `sensor.battery1_current` | — | **Edge case penting**, lihat O.3 |
| **Juwei Energy Meter CW24/CW20** | — | `sensor.ju_wei_dian_neng_biao_cw24_cw20_power` (saat ini `unavailable`) | idem | idem | — | Source yang sedang offline — contoh nyata skenario K.1/K.2, bagus untuk test availability handling |

**Rekomendasi konkret untuk config awal**: buat Billing Group `PLN_HOME` → measurement source **MCB RUMAH**, dan `PLN_TOKO`/`PLN_SHOP` → measurement source **MCB TOKO** — ini persis skenario yang Anda contohkan sendiri di prinsip desain #3 (PLN_HOME/PLN_SHOP), dan sekarang sudah punya entity_id nyata untuk dipetakan langsung.

### O.3 Edge case nyata yang ditemukan (konfirmasi langsung terhadap Bagian K)

- **`sensor.battery1_total_energy_meter`** (energi netted battery) **device_class: energy tapi TANPA state_class sama sekali** (bukan `total_increasing`, bukan `measurement` — kosong). Ini konfirmasi nyata kenapa Source Normalization (C, F.1) tidak boleh asumsi entity upstream selalu punya `state_class` yang benar — normalization layer wajib validasi & fallback secara eksplisit, bukan cuma pass-through.
- **`sensor.mcb_rumah_phase_a_power`** pakai satuan **kW**, sementara source lain (MCB TONGOU, colokan) pakai **W** — konfirmasi nyata bahwa Energy Calculation (F.1) wajib normalisasi satuan (W↔kW) sebelum kalkulasi, jangan asumsikan semua source konsisten satuan.
- **`sensor.0x385b44fffed7fa8d_energy_cost`** ("sensor Cost", device_class `monetary`, unit `IDR`, state saat ini `unavailable` dengan attribute `"restored": true`) — ini **konfirmasi nyata** temuan riset B.3 soal bug Energy Dashboard auto-generated `_cost` sensor yang reset/rusak setelah restart (GitHub core issue #124167). Bukti langsung kenapa Cost Engine kita (Bagian F.3) sengaja TIDAK bergantung pada mekanisme auto-cost bawaan Energy Dashboard, melainkan bikin `cost_total` sendiri yang persist dengan benar.
- **`sensor.ju_wei_dian_neng_biao_cw24_cw20_*`** sedang `unavailable` — contoh live untuk test skenario K.1/K.2 (source unavailable), tidak perlu disimulasikan, tinggal pakai source ini sebagai kasus uji nyata kalau mau.

### O.4 Entity yang TIDAK BOLEH pernah disentuh sistem ini (kontrol/relay — non-negotiable)

Ditemukan eksplisit di instance user, wajib di-exclude total dari integration ini (baca-saja prinsip Bagian A tetap berlaku ketat):
`switch.0x385b44fffed7fa8d` (relay utama MCB TONGOU), `switch.mcb_rumah_switch`, `switch.mcb_toko_switch`, `switch.0x385b44fffed7fa8d_temperature_breaker`, `switch.0x385b44fffed7fa8d_power_breaker`, `switch.0x385b44fffed7fa8d_over_current_breaker`, `switch.0x385b44fffed7fa8d_over_voltage_breaker`, `switch.0x385b44fffed7fa8d_under_voltage_breaker`, `switch.mcb_tongou_child_lock`, dan seluruh entity `number.0x385b44fffed7fa8d_*_threshold`/`number.mcb_tongou_countdown` (itu threshold proteksi bawaan device sendiri, bukan milik sistem kita). Config flow integration ini sebaiknya bahkan tidak menampilkan entity domain `switch`/`number`/`select` dari device yang sama sebagai opsional field, supaya tidak ada risiko salah pilih.

### O.5 Sudah ada automation lain yang terkait — jangan duplikasi

User sudah punya automation existing: `automation.notifikasi_pln_padam`, `automation.notifikasi_mcb_utama_padam`/`_nyala`, `automation.notif_battery_menyala` (LISTRIK PLN PADAM/NYALA) — ini soal **deteksi listrik padam/nyala**, beda concern dari token/cost monitor kita. Notification Engine (Bagian I) kita tetap jalan independen, tapi sebaiknya disebutkan ke user saat setup supaya pesan Telegram dari dua sistem berbeda ini tidak membingungkan (mis. beri prefix pesan yang jelas seperti "[Token PLN]" vs automation lain yang sudah pakai "NOTIFIKASI - ...").

---

*Akhir dokumen. Siap dipakai sebagai blueprint implementasi oleh Claude Code. Dua hal yang sebelumnya ditandai UNKNOWN — perilaku `total_increasing` saat reset (F.2) dan API purge statistics (N.4) — sudah diverifikasi tuntas langsung ke source code HA Core 2026.8.3 sebelum dokumen ini diserahkan ke produksi; F.2 bahkan menemukan dan memperbaiki bug desain nyata di Token Engine (G.1). Claude Code tinggal mengimplementasikan sesuai algoritma yang sudah pasti ini, dengan unit test untuk memverifikasi implementasinya benar (L.4) — bukan lagi riset ulang perilakunya. Bagian O memberi ground truth entity nyata — pakai itu sebagai contoh konkret di config flow dan test, bukan data fiktif.*
