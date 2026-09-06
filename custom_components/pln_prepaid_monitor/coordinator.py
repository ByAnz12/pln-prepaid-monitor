"""Runtime per Energy Source: mendengarkan sumber, menormalkan, mengakumulasi.

Ini bukan ``DataUpdateCoordinator`` bergaya polling, karena tidak ada apa pun
yang perlu di-poll: seluruh data datang dari entity lain di Home Assistant yang
sudah punya mekanisme update sendiri. Kita berlangganan perubahan state
(event-driven) supaya tidak menambah beban dan tidak memperlambat pembacaan.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_NAME,
    STATE_OFF,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_time,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CHANNEL_CONF_KEYS,
    CHANNEL_CURRENT,
    CHANNEL_ENERGY,
    CHANNEL_FREQUENCY,
    CHANNEL_POWER,
    CHANNEL_VOLTAGE,
    AUTO_PURGE_INTERVAL_HOURS,
    CONF_AUTO_PURGE_ENABLED,
    CONF_AVAILABILITY_ENTITY_ID,
    CONF_CYCLE_PERIODS,
    CONF_ENABLED,
    CONF_RATE_HISTORY,
    CONF_RESET_HOLD_THRESHOLD_KWH,
    CONF_SOURCE_IDS,
    CONF_TOKEN_PRESETS,
    CONF_TARIFF_ID,
    CONF_TOKEN_ENABLED,
    CONF_UNAVAILABLE_GRACE_MINUTES,
    CONF_STATISTICS_RETENTION_YEARS,
    DEFAULT_AUTO_PURGE_ENABLED,
    DEFAULT_CYCLE_PERIODS,
    DEFAULT_RESET_HOLD_THRESHOLD_KWH,
    DEFAULT_STATISTICS_RETENTION_YEARS,
    DEFAULT_TOKEN_ENABLED,
    DOMAIN,
    PREDICTION_REFRESH_MINUTES,
    DEFAULT_UNAVAILABLE_GRACE_MINUTES,
    SOURCE_OF_TRUTH_CUMULATIVE,
    SOURCE_OF_TRUTH_INTEGRATED,
    STORAGE_KEY,
    STORAGE_SAVE_DELAY_SECONDS,
    STORAGE_VERSION,
    SUBENTRY_TYPE_BILLING_GROUP,
    SUBENTRY_TYPE_ENERGY_SOURCE,
    SUBENTRY_TYPE_TARIFF,
)
from .engines.accumulator import (
    AccumulatorEvent,
    AccumulatorState,
    IntegratorState,
    PowerIntegrator,
    ResetSafeAccumulator,
)
from .engines.cost_engine import (
    CostAccumulator,
    CostTotalState,
    TariffConfig,
    fixed_charge_accrued,
)
from .engines.energy_calc import (
    GroupTotal,
    GroupTotalState,
    PeriodCounter,
    PeriodCounterState,
)
from .engines.normalization import CHANNEL_SPECS, conversion_factor
from .engines.notification_engine import NotificationConfig, NotifierState
from .engines.prediction_engine import (
    STATUS_UNKNOWN,
    PredictionConfig,
    PredictionResult,
    TokenThresholds,
    determine_status,
    predict,
)
from .engines.token_engine import (
    TokenLedger,
    TokenLedgerState,
    TokenPreset,
    load_presets,
)
from .engines.usage_table import (
    DEFAULT_MAX_ROWS,
    DIRECTION_DESC,
    GRAIN_DAY,
    GRAIN_MONTH,
    UsageQuery,
    UsageTable,
    SORT_TIME,
    clamp_view,
    range_bounds,
)
from .statistics_helper import async_fetch_range, async_fetch_window_samples
from .engines.period import ALL_PERIODS, CycleConfig, cycle_start, next_cycle_start

_LOGGER = logging.getLogger(__name__)

_MEASUREMENT_CHANNELS = (
    CHANNEL_POWER,
    CHANNEL_VOLTAGE,
    CHANNEL_CURRENT,
    CHANNEL_FREQUENCY,
)

# Seberapa sering daya disampel ulang untuk sumber yang tidak punya sensor kWh.
#
# Kenapa ini perlu, padahal kita sudah mendengarkan perubahan state: Home
# Assistant hanya mengirim event "state changed" kalau nilainya benar-benar
# berubah. Sensor daya yang melaporkan angka sama berulang kali (beban stabil)
# tidak memicu event apa pun, dan tanpa sampler ini akumulasi energinya akan
# berhenti diam-diam. Sumber yang punya sensor kWh asli tidak terpengaruh.
POWER_SAMPLE_INTERVAL = timedelta(seconds=30)

# Seberapa jauh harga efektif hasil hitungan harus meleset dari harga yang
# berlaku sebelum user ditanya. Di bawah ini selisihnya berasal dari
# pembulatan struk, bukan dari perubahan harga.
RATE_CHANGE_TOLERANCE = 0.0025


class SourceRuntime:
    """Menjaga state satu Energy Source selama Home Assistant berjalan."""

    def __init__(
        self,
        hass: HomeAssistant,
        subentry_id: str,
        config: dict[str, Any],
        stored: dict[str, Any] | None,
        on_persist: Callable[[], None],
    ) -> None:
        """Siapkan runtime dari konfigurasi subentry dan state tersimpan."""
        self.hass = hass
        self.subentry_id = subentry_id
        self.config = config
        self.name: str = str(config.get(CONF_NAME, "")) or subentry_id
        self._on_persist = on_persist

        stored = stored or {}
        self.accumulator = ResetSafeAccumulator(
            AccumulatorState.from_dict(stored.get("accumulator")),
            seed_offset=True,
        )
        self.integrator = PowerIntegrator(
            IntegratorState.from_dict(stored.get("integrator"))
        )

        self.energy_entity_id: str | None = config.get(
            CHANNEL_CONF_KEYS[CHANNEL_ENERGY]
        )
        self.availability_entity_id: str | None = config.get(
            CONF_AVAILABILITY_ENTITY_ID
        )
        self.grace_seconds: float = (
            float(
                config.get(
                    CONF_UNAVAILABLE_GRACE_MINUTES, DEFAULT_UNAVAILABLE_GRACE_MINUTES
                )
            )
            * 60
        )

        # Nilai terakhir per kanal, sudah dalam satuan kanonik.
        self.values: dict[str, float | None] = dict.fromkeys(CHANNEL_CONF_KEYS)
        self.raw_values: dict[str, float | None] = dict.fromkeys(CHANNEL_CONF_KEYS)
        self.units: dict[str, str | None] = dict.fromkeys(CHANNEL_CONF_KEYS)
        self.state_classes: dict[str, str | None] = dict.fromkeys(CHANNEL_CONF_KEYS)
        self.factors: dict[str, float] = dict.fromkeys(CHANNEL_CONF_KEYS, 1.0)

        self.source_ok = False
        self.unavailable_since: str | None = None
        self._grace_unsub: CALLBACK_TYPE | None = None
        self._unsubscribe: CALLBACK_TYPE | None = None
        self._sampler_unsub: CALLBACK_TYPE | None = None
        self._listeners: list[CALLBACK_TYPE] = []

    # ------------------------------------------------------------------
    # sifat yang dibaca entity
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Apakah source ini diaktifkan user."""
        return bool(self.config.get(CONF_ENABLED, True))

    @property
    def uses_cumulative_energy(self) -> bool:
        """True bila angka kWh berasal dari sensor kumulatif asli."""
        return bool(self.energy_entity_id)

    @property
    def source_of_truth(self) -> str:
        """Asal-usul angka energi, dipublikasikan sebagai atribut entity."""
        if self.uses_cumulative_energy:
            return SOURCE_OF_TRUTH_CUMULATIVE
        return SOURCE_OF_TRUTH_INTEGRATED

    @property
    def available(self) -> bool:
        """Available selama sumber sehat, atau masih dalam masa tenggang."""
        return self.source_ok or self._grace_unsub is not None

    @property
    def holding_last_value(self) -> bool:
        """True bila sumber sedang hilang tapi kita masih menahan nilai lama."""
        return not self.source_ok and self._grace_unsub is not None

    @property
    def energy_kwh(self) -> float | None:
        """Total energi kanonik dalam kWh."""
        if self.uses_cumulative_energy:
            return self.accumulator.state.total
        if self.integrator.state.last_timestamp is None and not self.integrator.state.total_kwh:
            return None
        return self.integrator.state.total_kwh

    # ------------------------------------------------------------------
    # daur hidup
    # ------------------------------------------------------------------

    @callback
    def async_add_listener(self, update_callback: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Daftarkan entity yang ingin diberi tahu saat ada perubahan."""
        self._listeners.append(update_callback)

        @callback
        def _remove() -> None:
            if update_callback in self._listeners:
                self._listeners.remove(update_callback)

        return _remove

    @callback
    def _async_notify(self) -> None:
        """Beri tahu semua entity bahwa ada nilai baru."""
        for update_callback in list(self._listeners):
            update_callback()

    @callback
    def async_start(self) -> None:
        """Baca state awal lalu berlangganan perubahan."""
        tracked = self._tracked_entity_ids()
        for entity_id in tracked:
            state = self.hass.states.get(entity_id)
            if state is not None:
                self._ingest(entity_id, state.state, state.attributes)
        self._evaluate_availability(initial=True)

        if tracked:
            self._unsubscribe = async_track_state_change_event(
                self.hass, tracked, self._handle_state_event
            )

        if not self.uses_cumulative_energy:
            self._sampler_unsub = async_track_time_interval(
                self.hass, self._async_sample_power, POWER_SAMPLE_INTERVAL
            )

    @callback
    def async_stop(self) -> None:
        """Lepas semua langganan dan timer."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._sampler_unsub is not None:
            self._sampler_unsub()
            self._sampler_unsub = None
        self._cancel_grace()
        self._listeners.clear()

    @callback
    def _async_sample_power(self, _now: datetime) -> None:
        """Sampel daya secara berkala untuk sumber tanpa sensor kWh."""
        if not self.source_ok:
            self.integrator.pause()
            return
        power = self.values.get(CHANNEL_POWER)
        if power is None:
            return
        self.integrator.add_sample(power, dt_util.utcnow().timestamp())
        self._async_notify()
        self._on_persist()

    def _tracked_entity_ids(self) -> list[str]:
        """Semua entity sumber yang perlu dipantau."""
        entity_ids = [
            entity_id
            for conf_key in CHANNEL_CONF_KEYS.values()
            if (entity_id := self.config.get(conf_key))
        ]
        if self.availability_entity_id:
            entity_ids.append(self.availability_entity_id)
        return entity_ids

    # ------------------------------------------------------------------
    # pemrosesan state
    # ------------------------------------------------------------------

    @callback
    def _handle_state_event(self, event: Event[EventStateChangedData]) -> None:
        """Tangani satu perubahan state dari entity sumber."""
        new_state = event.data["new_state"]
        entity_id = event.data["entity_id"]
        if new_state is None:
            self._evaluate_availability()
            self._async_notify()
            return

        self._ingest(entity_id, new_state.state, new_state.attributes)
        self._evaluate_availability()
        self._async_notify()
        self._on_persist()

    def _roles_for_entity(self, entity_id: str) -> list[str]:
        """Peran apa saja yang dipegang entity ini (bisa lebih dari satu)."""
        return [
            role
            for role, conf_key in CHANNEL_CONF_KEYS.items()
            if self.config.get(conf_key) == entity_id
        ]

    def _ingest(
        self, entity_id: str, state_value: str, attributes: dict[str, Any]
    ) -> None:
        """Normalkan satu pembacaan dan masukkan ke kanal yang sesuai."""
        for role in self._roles_for_entity(entity_id):
            spec = CHANNEL_SPECS[role]
            unit = attributes.get("unit_of_measurement")
            self.units[role] = unit
            self.state_classes[role] = attributes.get("state_class")

            factor, _issue = conversion_factor(spec, unit)
            if factor is None:
                # Satuan tidak bisa dikonversi: jangan menebak, hentikan kanal.
                _LOGGER.warning(
                    "Satuan %s pada %s tidak bisa dikonversi ke %s, kanal %s diabaikan",
                    unit,
                    entity_id,
                    spec.target_unit,
                    role,
                )
                self.values[role] = None
                continue
            self.factors[role] = factor

            if state_value in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                self.raw_values[role] = None
                # Nilai lama sengaja TIDAK dihapus: itulah "hold last value"
                # untuk skenario K.1.
                continue

            try:
                raw = float(state_value)
            except (TypeError, ValueError):
                self.raw_values[role] = None
                continue

            self.raw_values[role] = raw
            normalized = raw * factor
            self.values[role] = normalized

            if role == CHANNEL_ENERGY:
                self._ingest_energy(entity_id, normalized)
            elif role == CHANNEL_POWER and not self.uses_cumulative_energy:
                # Fallback Riemann: setiap sampel daya baru menutup interval
                # sebelumnya. Daya lama berlaku sampai pembacaan ini datang.
                self.integrator.add_sample(normalized, dt_util.utcnow().timestamp())

    def _ingest_energy(self, entity_id: str, value_kwh: float) -> None:
        """Masukkan pembacaan kWh ke akumulator aman-reset."""
        event = self.accumulator.update(value_kwh, dt_util.utcnow().isoformat())
        if event is AccumulatorEvent.RESET:
            state = self.accumulator.state
            # Sengaja hanya log (bukan notifikasi Telegram) sesuai spec K.5,
            # supaya tidak jadi sumber spam pesan.
            _LOGGER.warning(
                "Reset counter terdeteksi pada %s (sumber '%s'): nilai turun dari %s "
                "ke %s kWh. Siklus lama sudah ditutup, akumulasi dilanjutkan tanpa "
                "konsumsi negatif. Ini reset ke-%s sejak pemantauan dimulai",
                entity_id,
                self.name,
                state.last_reset_from,
                value_kwh,
                state.resets_detected,
            )
        elif event is AccumulatorEvent.NEGATIVE_IGNORED:
            _LOGGER.warning(
                "Pembacaan negatif dari %s (sumber '%s') diabaikan: %s",
                entity_id,
                self.name,
                value_kwh,
            )

    # ------------------------------------------------------------------
    # ketersediaan sumber
    # ------------------------------------------------------------------

    def _availability_entity_ok(self) -> bool:
        """Baca entity availability eksplisit bila user memetakannya."""
        if not self.availability_entity_id:
            return True
        state = self.hass.states.get(self.availability_entity_id)
        if state is None:
            return False
        return state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN, STATE_OFF)

    def _primary_entity_ok(self) -> bool:
        """Sumber dianggap sehat bila kanal utamanya memberi angka."""
        primary_role = CHANNEL_ENERGY if self.uses_cumulative_energy else CHANNEL_POWER
        entity_id = self.config.get(CHANNEL_CONF_KEYS[primary_role])
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return False
        try:
            float(state.state)
        except (TypeError, ValueError):
            return False
        return True

    @callback
    def _evaluate_availability(self, initial: bool = False) -> None:
        """Tentukan status sehat/tidak, dengan masa tenggang configurable."""
        ok = self._primary_entity_ok() and self._availability_entity_ok()
        if ok == self.source_ok and not initial:
            return

        self.source_ok = ok
        if ok:
            self._cancel_grace()
            self.unavailable_since = None
            return

        # Sumber hilang: mulai masa tenggang. Selama itu nilai lama ditahan,
        # bukan dijatuhkan ke nol (spec K.1).
        if not self.uses_cumulative_energy:
            self.integrator.pause()
        self.unavailable_since = dt_util.utcnow().isoformat()
        if self._grace_unsub is None and self.grace_seconds > 0:
            self._grace_unsub = async_call_later(
                self.hass, self.grace_seconds, self._grace_expired
            )
        elif self.grace_seconds <= 0:
            self._async_notify()

    @callback
    def _grace_expired(self, _now: Any) -> None:
        """Masa tenggang habis: sumber dinyatakan benar-benar offline."""
        self._grace_unsub = None
        _LOGGER.warning(
            "Sumber '%s' masih tidak tersedia setelah %s menit, ditandai offline",
            self.name,
            self.grace_seconds / 60,
        )
        self._async_notify()

    @callback
    def _cancel_grace(self) -> None:
        """Batalkan timer masa tenggang."""
        if self._grace_unsub is not None:
            self._grace_unsub()
            self._grace_unsub = None

    # ------------------------------------------------------------------
    # persistensi
    # ------------------------------------------------------------------

    def as_stored(self) -> dict[str, Any]:
        """Snapshot yang disimpan ke .storage supaya selamat dari restart."""
        return {
            "accumulator": self.accumulator.state.as_dict(),
            "integrator": self.integrator.state.as_dict(),
        }


class BillingGroupRuntime:
    """Menggabungkan beberapa Energy Source dan menghitung pemakaian per periode."""

    def __init__(
        self,
        hass: HomeAssistant,
        subentry_id: str,
        config: dict[str, Any],
        stored: dict[str, Any] | None,
        sources: dict[str, SourceRuntime],
        on_persist: Callable[[], None],
        tariff_data: dict[str, Any] | None = None,
    ) -> None:
        """Siapkan runtime Billing Group dari konfigurasi dan state tersimpan."""
        self.hass = hass
        self.subentry_id = subentry_id
        self.config = config
        self.name: str = str(config.get(CONF_NAME, "")) or subentry_id
        self._on_persist = on_persist

        stored = stored or {}
        self.source_ids: list[str] = list(config.get(CONF_SOURCE_IDS) or [])
        # Sumber yang dinonaktifkan atau sudah dihapus tidak ikut - grup tetap
        # berfungsi sebagian, sesuai spec K.6.
        self.members: dict[str, SourceRuntime] = {
            source_id: sources[source_id]
            for source_id in self.source_ids
            if source_id in sources
        }

        self.cycle_config = CycleConfig.from_dict(config)
        self.periods: list[str] = [
            period
            for period in (config.get(CONF_CYCLE_PERIODS) or DEFAULT_CYCLE_PERIODS)
            if period in ALL_PERIODS
        ]

        self.group_total = GroupTotal(GroupTotalState.from_dict(stored.get("total")))
        stored_counters = stored.get("counters") or {}
        self.counters: dict[str, PeriodCounter] = {
            period: PeriodCounter(
                period,
                self.cycle_config,
                PeriodCounterState.from_dict(stored_counters.get(period)),
            )
            for period in self.periods
        }

        # Tarif bersifat opsional: kelompok tanpa tarif tetap menghitung energi,
        # hanya tidak punya sensor biaya.
        self.tariff_data = tariff_data
        self.tariff: TariffConfig | None = (
            TariffConfig.from_dict(tariff_data) if tariff_data else None
        )
        self.tariff_name: str | None = (
            str(tariff_data.get(CONF_NAME, "")) if tariff_data else None
        )
        self.rate_history: list[dict[str, Any]] = list(
            (tariff_data or {}).get(CONF_RATE_HISTORY) or []
        )
        self.cost = CostAccumulator(CostTotalState.from_dict(stored.get("cost")))
        stored_cost_counters = stored.get("cost_counters") or {}
        self.cost_counters: dict[str, PeriodCounter] = {
            period: PeriodCounter(
                period,
                self.cycle_config,
                PeriodCounterState.from_dict(stored_cost_counters.get(period)),
            )
            for period in self.periods
        }

        self.ledger = TokenLedger(TokenLedgerState.from_dict(stored.get("token")))
        self.prediction_config = PredictionConfig.from_dict(config)
        self.thresholds = TokenThresholds.from_dict(config)
        self.prediction = PredictionResult()
        self.notification_config = NotificationConfig.from_dict(config)
        self.notifier_state = NotifierState.from_dict(stored.get("notifier"))
        self._prediction_unsub: CALLBACK_TYPE | None = None

        # Angka yang sedang diketik user di dashboard, sebelum tombolnya
        # ditekan. Disimpan di sini, bukan di entity, supaya tombol dan isian
        # membaca satu angka yang sama - dan supaya angkanya tidak hilang kalau
        # Home Assistant restart di tengah-tengah.
        # Rincian per periode, diisi dari long-term statistics tiap kali
        # prediksi dihitung ulang.
        self.summaries: dict[str, dict[str, Any]] = {"energy": {}, "cost": {}}

        # Tabel riwayat yang sedang tampil. Dihitung ulang tiap kali user
        # mengubah salah satu kendalinya, dan ikut disegarkan bersama prediksi.
        self.usage_table: UsageTable = UsageTable()

        # Usulan perubahan harga per kWh yang menunggu keputusan user. Sengaja
        # tidak langsung diterapkan: harga adalah angka yang user tetapkan
        # sendiri, dan mengubahnya diam-diam berarti seluruh biaya berikutnya
        # dihitung dengan angka yang tidak pernah mereka setujui.
        self.entry_id: str | None = None
        self.pending_rate: dict[str, Any] | None = (
            dict(stored["pending_rate"]) if stored.get("pending_rate") else None
        )

        # Isian yang berupa teks (nama template, template yang sedang dipilih).
        # Dipisah dari ``inputs`` yang berisi angka supaya tidak ada nilai
        # bercampur tipe di satu tempat.
        self.inputs_text: dict[str, str] = {
            str(key): str(value)
            for key, value in (stored.get("inputs_text") or {}).items()
        }

        self.inputs: dict[str, float] = {
            str(key): float(value)
            for key, value in (stored.get("inputs") or {}).items()
        }

        self._member_unsubs: list[CALLBACK_TYPE] = []
        self._timer_unsubs: dict[str, CALLBACK_TYPE] = {}
        self._listeners: list[CALLBACK_TYPE] = []

    # ------------------------------------------------------------------
    # sifat yang dibaca entity
    # ------------------------------------------------------------------

    @property
    def total_kwh(self) -> float | None:
        """Total kWh gabungan seluruh anggota."""
        return self.group_total.state.total

    @property
    def power_w(self) -> float | None:
        """Daya gabungan saat ini, dalam Watt."""
        values = [
            runtime.values.get(CHANNEL_POWER)
            for runtime in self.members.values()
            if runtime.values.get(CHANNEL_POWER) is not None
        ]
        if not values:
            return None
        return sum(values)

    @property
    def member_names(self) -> list[str]:
        """Nama semua anggota grup."""
        return [runtime.name for runtime in self.members.values()]

    @property
    def unavailable_member_names(self) -> list[str]:
        """Anggota yang sedang tidak terhubung.

        Ditampilkan sebagai atribut, bukan disembunyikan: kalau satu meteran
        mati, pemakaiannya memang tidak terhitung, dan user berhak tahu itu.
        """
        return [
            runtime.name
            for runtime in self.members.values()
            if not runtime.available
        ]

    def period_value(self, period: str) -> float | None:
        """Pemakaian pada periode tertentu, atau None bila belum ada data."""
        counter = self.counters.get(period)
        if counter is None:
            return None
        return counter.value(self.total_kwh)

    def period_cycle_start(self, period: str) -> datetime | None:
        """Awal siklus berjalan untuk periode tertentu."""
        counter = self.counters.get(period)
        if counter is None:
            return None
        return counter.cycle_start_at

    def period_covers_full_cycle(self, period: str) -> bool:
        """Apakah penghitung ini sudah mencakup siklusnya dari awal.

        False pada siklus pertama sesudah pemasangan: angkanya nyata, tapi
        rentangnya lebih pendek daripada yang disiratkan namanya, jadi tidak
        adil dibandingkan dengan siklus berikutnya.
        """
        counter = self.counters.get(period)
        if counter is None:
            return False
        return counter.covers_full_cycle(dt_util.now())

    # ------------------------------------------------------------------
    # biaya
    # ------------------------------------------------------------------

    @property
    def has_cost(self) -> bool:
        """True bila kelompok ini punya tarif, sehingga biaya bisa dihitung."""
        return self.tariff is not None

    @property
    def active_rate(self) -> float | None:
        """Tarif Rp/kWh yang berlaku sekarang."""
        return self.tariff.rate_rp_per_kwh if self.tariff else None

    @property
    def cost_total_rp(self) -> float | None:
        """Total biaya berjalan, murni dari energi yang dipakai."""
        if not self.has_cost or self.cost.state.energy_prev is None:
            return None
        return self.cost.state.total_rp

    def cost_period_value(self, period: str) -> float | None:
        """Biaya energi pada periode berjalan, belum termasuk biaya beban."""
        if not self.has_cost:
            return None
        counter = self.cost_counters.get(period)
        if counter is None:
            return None
        return counter.value(self.cost_total_rp)

    def cost_period_fixed_charge(self, period: str) -> float:
        """Biaya beban yang sudah berjalan pada periode ini.

        Hanya berlaku untuk periode bulanan dan tahunan (spec F.3): penghitung
        jam, hari, dan minggu sengaja tetap murni berisi biaya energi.
        """
        if not self.has_cost or period not in ("month", "year"):
            return 0.0
        counter = self.cost_counters.get(period)
        if counter is None:
            return 0.0
        # Sengaja memakai batas siklus SEBENARNYA, bukan titik mulai
        # penghitungnya. Keduanya berbeda pada siklus pertama sesudah
        # pemasangan, dan yang benar di sini adalah batas siklus: PLN menagih
        # biaya beban untuk sebulan penuh, tidak peduli kapan integrasi ini
        # dipasang. Menghitungnya sejak pemasangan akan menagih terlalu
        # sedikit di bulan pertama, lalu terlihat "normal" bulan berikutnya -
        # selisih yang tidak akan pernah ada yang menyadarinya.
        now = dt_util.now()
        return fixed_charge_accrued(
            self.tariff, cycle_start(period, now, self.cycle_config), now
        )

    def cost_period_total(self, period: str) -> float | None:
        """Biaya periode berjalan termasuk biaya beban bila ada."""
        energy_cost = self.cost_period_value(period)
        if energy_cost is None:
            return None
        return energy_cost + self.cost_period_fixed_charge(period)

    # ------------------------------------------------------------------
    # daur hidup
    # ------------------------------------------------------------------

    @callback
    def async_add_listener(self, update_callback: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Daftarkan entity yang ingin diberi tahu saat ada perubahan."""
        self._listeners.append(update_callback)

        @callback
        def _remove() -> None:
            if update_callback in self._listeners:
                self._listeners.remove(update_callback)

        return _remove

    @callback
    def _async_notify(self) -> None:
        """Beri tahu semua entity bahwa ada nilai baru."""
        for update_callback in list(self._listeners):
            update_callback()

    @callback
    def async_start(self) -> None:
        """Ikuti perubahan anggota dan pasang timer batas siklus."""
        for runtime in self.members.values():
            self._member_unsubs.append(
                runtime.async_add_listener(self._handle_member_update)
            )
        self._refresh()
        for period in self.periods:
            self._schedule_boundary(period)

    @callback
    def async_stop(self) -> None:
        """Lepas semua langganan dan timer."""
        for unsub in self._member_unsubs:
            unsub()
        self._member_unsubs.clear()
        for unsub in self._timer_unsubs.values():
            unsub()
        self._timer_unsubs.clear()
        if self._prediction_unsub is not None:
            self._prediction_unsub()
            self._prediction_unsub = None
        self._listeners.clear()

    @callback
    def _handle_member_update(self) -> None:
        """Salah satu anggota melaporkan angka baru."""
        self._refresh()
        self._async_notify()
        self._on_persist()

    @callback
    def _refresh(self) -> None:
        """Hitung ulang total gabungan dan seluruh penghitung periode."""
        now = dt_util.now()
        # Dicatat sebelum diperbarui: kalau ternyata ada reset counter besar,
        # inilah titik yang dipakai membekukan ledger token - bukan angka
        # sesudahnya, yang sudah menyerap lonjakannya.
        total_before_update = self.group_total.state.total
        total = self.group_total.update(
            {
                source_id: runtime.energy_kwh
                for source_id, runtime in self.members.items()
            }
        )
        for period, counter in self.counters.items():
            if counter.sync(total, now):
                _LOGGER.debug(
                    "Siklus %s untuk '%s' berganti, penghitung dimulai dari nol",
                    period,
                    self.name,
                )

        self._check_for_ledger_hold(total_before_update)

        if self.tariff is None:
            return

        # Biaya dihitung dengan tarif yang berlaku SEKARANG, di saat pemakaian
        # itu tercatat. Kalau tarif berubah nanti, angka yang sudah terhitung
        # tidak diutak-atik lagi (spec K.7).
        cost_total = self.cost.update(total, self.tariff.rate_rp_per_kwh)
        for counter in self.cost_counters.values():
            counter.sync(cost_total, now)

    @callback
    def _schedule_boundary(self, period: str) -> None:
        """Pasang timer tepat di batas siklus berikutnya."""
        if (existing := self._timer_unsubs.pop(period, None)) is not None:
            existing()

        boundary = next_cycle_start(period, dt_util.now(), self.cycle_config)

        @callback
        def _boundary_reached(_now: datetime) -> None:
            self._timer_unsubs.pop(period, None)
            self._refresh()
            self._async_notify()
            self._on_persist()
            self._schedule_boundary(period)

        self._timer_unsubs[period] = async_track_point_in_time(
            self.hass, _boundary_reached, boundary
        )

    # ------------------------------------------------------------------
    # token
    # ------------------------------------------------------------------

    @property
    def token_enabled(self) -> bool:
        """Apakah pencatatan token diaktifkan untuk kelompok ini."""
        return bool(self.config.get(CONF_TOKEN_ENABLED, DEFAULT_TOKEN_ENABLED))

    @property
    def token_remaining_kwh(self) -> float | None:
        """Sisa token dalam kWh."""
        if not self.token_enabled:
            return None
        return self.ledger.remaining_kwh(self.total_kwh)

    @property
    def token_remaining_value_rp(self) -> float | None:
        """Perkiraan nilai sisa token dalam Rupiah.

        Ini **bukan** jumlah uang yang perlu dibayar untuk membeli kWh sebanyak
        itu: pembelian token baru akan dipotong biaya admin dan PPJ dulu
        (spec F.3 dan B.2).
        """
        remaining = self.token_remaining_kwh
        if remaining is None or self.tariff is None:
            return None
        return remaining * self.tariff.rate_rp_per_kwh

    @property
    def token_consumed_kwh(self) -> float | None:
        """Pemakaian sejak titik awal ledger token."""
        if not self.token_enabled:
            return None
        return self.ledger.consumed_kwh(self.total_kwh)

    @property
    def token_presets(self) -> list[TokenPreset]:
        """Nilai pengisian siap pakai yang diatur user untuk kelompok ini."""
        return load_presets(self.config.get(CONF_TOKEN_PRESETS))

    @property
    def reset_hold_threshold_kwh(self) -> float:
        """Ambang pembacaan pasca-reset yang memicu penahanan ledger."""
        try:
            return float(
                self.config.get(
                    CONF_RESET_HOLD_THRESHOLD_KWH, DEFAULT_RESET_HOLD_THRESHOLD_KWH
                )
            )
        except (TypeError, ValueError):
            return DEFAULT_RESET_HOLD_THRESHOLD_KWH

    @callback
    def _check_for_ledger_hold(self, total_before_update: float | None) -> None:
        """Tahan ledger kalau ada reset counter yang cukup besar (lihat D-007).

        Yang menentukan bahaya bukan sedalam apa angkanya jatuh, melainkan
        **berapa nilai pembacaan pertama sesudah reset** - karena angka itulah
        yang akan langsung terhitung penuh sebagai pemakaian baru.
        """
        if not self.token_enabled or not self.ledger.started:
            return

        threshold = self.reset_hold_threshold_kwh
        for source_id, runtime in self.members.items():
            state = runtime.accumulator.state
            seen = self.ledger.state.seen_resets.get(source_id, 0)
            if state.resets_detected <= seen:
                continue

            self.ledger.state.seen_resets[source_id] = state.resets_detected
            reset_to = state.last_reset_to
            if reset_to is None or reset_to <= threshold:
                # Reset firmware biasa yang jatuh ke hampir nol: dampaknya bisa
                # diabaikan, ledger jalan terus tanpa mengganggu user.
                _LOGGER.info(
                    "Reset kecil pada '%s' (%s kWh) tidak menahan ledger token '%s'",
                    runtime.name,
                    reset_to,
                    self.name,
                )
                continue

            _LOGGER.warning(
                "Ledger token '%s' ditahan: sumber '%s' melompat ke %s kWh sesudah "
                "reset counter, melebihi ambang %s kWh. Sisa token dibekukan sampai "
                "Anda memutuskan lewat layanan resolve_ledger_hold",
                self.name,
                runtime.name,
                reset_to,
                threshold,
            )
            self.ledger.engage_hold(
                source_name=runtime.name,
                reset_from=state.last_reset_from,
                reset_to=reset_to,
                group_total=(
                    total_before_update
                    if total_before_update is not None
                    else self.total_kwh
                ),
                timestamp=dt_util.now().isoformat(),
            )
            return

    # ------------------------------------------------------------------
    # tabel riwayat
    # ------------------------------------------------------------------

    @property
    def usage_query(self) -> UsageQuery:
        """Pilihan user yang sedang berlaku, sudah dibersihkan.

        Nilai yang tidak sah - misalnya tampilan yang lebih kasar daripada
        jenis waktunya, atau tanggal yang gagal dibaca - diganti nilai bawaan
        yang masuk akal, bukan dibiarkan menjatuhkan pembuatan tabel.
        """
        scope = self.inputs_text.get("usage_scope") or GRAIN_MONTH
        view = clamp_view(scope, self.inputs_text.get("usage_view") or GRAIN_DAY)
        today = dt_util.now().date()
        return UsageQuery(
            scope=scope,
            view=view,
            sort=self.inputs_text.get("usage_sort") or SORT_TIME,
            direction=self.inputs_text.get("usage_direction") or DIRECTION_DESC,
            start=self._usage_date("usage_from", today - timedelta(days=30)),
            end=self._usage_date("usage_to", today),
            max_rows=int(self.inputs.get("usage_rows") or DEFAULT_MAX_ROWS),
        )

    def _usage_date(self, key: str, fallback: date) -> date:
        """Satu batas rentang, atau nilai bawaan kalau isiannya belum diisi."""
        raw = self.inputs_text.get(key)
        if not raw:
            return fallback
        parsed = dt_util.parse_date(raw)
        return parsed if parsed is not None else fallback

    async def async_refresh_usage_table(self) -> None:
        """Hitung ulang tabel riwayat dari long-term statistics.

        Tabelnya selalu disusun dari statistik **harian**, lalu dikelompokkan
        ulang di engine sesuai tampilan yang dipilih. Membaca langsung per bulan
        atau per tahun memang bisa, tapi dua sumber angka untuk hal yang sama
        adalah dua tempat yang bisa berbeda - dan bedanya tidak akan kelihatan.
        """
        statistic_id = self.energy_total_statistic_id
        if statistic_id is None:
            self.usage_table = UsageTable()
            return

        query = self.usage_query
        first, last = range_bounds(query.scope, query.start, query.end)
        start = dt_util.start_of_local_day(first)
        # Batas akhir eksklusif di sisi recorder, jadi hari terakhir ikut
        # terbawa hanya kalau kita meminta sampai awal hari sesudahnya.
        end = dt_util.start_of_local_day(last + timedelta(days=1))

        energy = await async_fetch_range(self.hass, statistic_id, "day", start, end)
        cost = None
        if (cost_id := self.cost_total_statistic_id) is not None:
            cost = await async_fetch_range(self.hass, cost_id, "day", start, end)

        from .engines.usage_table import build_table  # noqa: PLC0415

        self.usage_table = build_table(
            query=query,
            energy=energy,
            cost=cost,
            labeller=self._usage_label,
        )
        self._async_notify()

    def _usage_label(self, key: tuple[int, ...], grain: str) -> str:
        """Teks periode yang dibaca user, mengikuti bahasa Home Assistant."""
        from .engines.usage_table import GRAIN_MONTH as _MONTH, GRAIN_YEAR as _YEAR  # noqa: PLC0415
        from .messages import month_names, pick_language  # noqa: PLC0415

        if grain == _YEAR:
            return str(key[0])
        names = month_names(pick_language(self.hass.config.language))
        if grain == _MONTH:
            return f"{names[key[1] - 1]} {key[0]}"
        return f"{key[2]:02d} {names[key[1] - 1]} {key[0]}"

    @callback
    def async_set_usage_control(self, key: str, value: str | float) -> None:
        """Simpan satu kendali tabel, lalu susun ulang tabelnya.

        Penyusunan ulang membaca database recorder, jadi tidak bisa dilakukan
        di dalam callback ini - ia dijadwalkan sebagai task tersendiri.
        """
        if isinstance(value, str):
            self.inputs_text[key] = value
        else:
            self.inputs[key] = float(value)
        self._on_persist()
        self.hass.async_create_task(self.async_refresh_usage_table())

    @callback
    def async_set_input(self, key: str, value: float) -> None:
        """Simpan angka yang sedang diketik user, dan beri tahu entity-nya."""
        self.inputs[key] = float(value)
        self._async_notify()
        self._on_persist()

    @callback
    def async_set_input_text(self, key: str, value: str) -> None:
        """Simpan isian teks, dan beri tahu entity yang menampilkannya."""
        self.inputs_text[key] = value
        self._async_notify()
        self._on_persist()

    def record_topup(
        self,
        *,
        kwh_credited: float,
        nominal_rp: float | None = None,
        timestamp: str | None = None,
        meter_reading_before: float | None = None,
        meter_reading_after: float | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Catat pengisian token, lalu beri tahu semua yang bergantung padanya.

        Satu-satunya tempat pencatatan pengisian dilakukan, dipakai bersama oleh
        layanan dan oleh tombol di dashboard - supaya keduanya tidak mungkin
        berperilaku berbeda.
        """
        if nominal_rp is None and (rate := self.active_rate):
            # Riwayat pengisian menampilkan kolom nominal. Kalau user hanya
            # mengisi kWh, nominalnya dihitung dari tarif yang berlaku SAAT INI
            # lalu disimpan - bukan dihitung ulang tiap kali ditampilkan.
            # Kenaikan tarif nanti tidak boleh menulis ulang harga pembelian
            # yang sudah lewat.
            nominal_rp = round(float(kwh_credited) * rate, 2)

        entry = self.ledger.add_topup(
            kwh_credited=kwh_credited,
            group_total=self.total_kwh,
            timestamp=timestamp or dt_util.now().isoformat(),
            nominal_rp=nominal_rp,
            meter_reading_before=meter_reading_before,
            meter_reading_after=meter_reading_after,
            note=note,
        )
        _LOGGER.info(
            "Token '%s' diisi %s kWh (id %s), sisa sekarang %s kWh",
            self.name,
            entry["kwh_credited"],
            entry["id"],
            self.token_remaining_kwh,
        )
        self._propose_rate_from(entry)
        self.async_ledger_changed()
        return entry

    def _propose_rate_from(self, entry: dict[str, Any]) -> None:
        """Kalau pengisian menyebut kWh dan nominal sekaligus, hitung harganya.

        Pengisian yang mencantumkan keduanya adalah struk: di sana tertulis
        berapa yang dibayar dan berapa kWh yang masuk, sudah termasuk admin,
        PPJ, dan materai. Bagi keduanya dan ketemu **harga efektif** per kWh.

        Yang dihasilkan cuma usulan. User yang memutuskan, lewat tombol Ya/Tidak
        di dashboard - lihat docs/decisions.md D-045.
        """
        current = self.active_rate
        kwh = float(entry.get("kwh_credited") or 0.0)
        nominal = entry.get("nominal_rp")
        if not current or nominal is None or kwh <= 0:
            return

        derived = round(float(nominal) / kwh, 2)
        if abs(derived - current) / current < RATE_CHANGE_TOLERANCE:
            # Selisih sekecil ini datang dari pembulatan, bukan dari perubahan
            # harga sungguhan. Bertanya untuk itu cuma jadi gangguan.
            return

        self.pending_rate = {
            "from_rate": current,
            "to_rate": derived,
            "kwh": kwh,
            "nominal_rp": float(nominal),
            "topup_id": entry.get("id"),
            "at": entry.get("timestamp"),
            # Perubahan sebesar ini hampir pasti salah ketik, bukan kenaikan
            # tarif. Ditandai supaya kartunya bisa memperingatkan, bukan
            # ditolak diam-diam - user tetap yang memutuskan.
            "implausible": not 0.5 <= derived / current <= 2.0,
        }
        _LOGGER.info(
            "Harga efektif '%s' terhitung %s/kWh (sebelumnya %s), menunggu keputusan",
            self.name,
            derived,
            current,
        )

    def resolve_rate_change(self, *, apply: bool) -> dict[str, Any] | None:
        """Terima atau tolak usulan harga. Mengembalikan usulan yang diputuskan."""
        proposal = self.pending_rate
        self.pending_rate = None
        self._async_notify()
        self._on_persist()
        return proposal if apply else None

    def calibrate_to(
        self,
        *,
        actual_remaining_kwh: float,
        timestamp: str | None = None,
        note: str | None = None,
    ) -> None:
        """Samakan ledger dengan angka di layar meteran fisik."""
        self.ledger.calibrate(
            actual_remaining_kwh=actual_remaining_kwh,
            group_total=self.total_kwh,
            timestamp=timestamp or dt_util.now().isoformat(),
            note=note,
        )
        _LOGGER.info(
            "Ledger token '%s' dikalibrasi ke %s kWh", self.name, actual_remaining_kwh
        )
        self.async_ledger_changed()

    @callback
    def async_ledger_changed(self) -> None:
        """Dipanggil sesudah layanan token mengubah ledger."""
        self._async_notify()
        self._on_persist()
        # Sisa token berubah, jadi perkiraan hari tersisa dan status ikut
        # berubah - dan itulah momen pesan pemulihan "token sudah terisi"
        # seharusnya dikirim.
        self.hass.async_create_task(self.async_refresh_prediction())

    # ------------------------------------------------------------------
    # prediksi
    # ------------------------------------------------------------------

    @property
    def energy_total_statistic_id(self) -> str | None:
        """entity_id sensor energi grup, yang jadi sumber statistik prediksi."""
        return er.async_get(self.hass).async_get_entity_id(
            "sensor", DOMAIN, f"{self.subentry_id}_energy_total"
        )

    @property
    def token_status(self) -> str:
        """Tingkat kegentingan token saat ini."""
        if not self.token_enabled:
            return STATUS_UNKNOWN
        return determine_status(
            days_remaining=self.prediction.days_remaining,
            remaining_kwh=self.token_remaining_kwh,
            thresholds=self.thresholds,
            on_hold=self.ledger.on_hold,
        )

    async def async_refresh_prediction(self, _now: datetime | None = None) -> None:
        """Hitung ulang perkiraan dari long-term statistics."""
        statistic_id = self.energy_total_statistic_id
        if statistic_id is None:
            return

        samples = await async_fetch_window_samples(
            self.hass, statistic_id, self.prediction_config, dt_util.now()
        )
        self.prediction = predict(
            remaining_kwh=self.token_remaining_kwh,
            samples_by_window=samples,
            config=self.prediction_config,
            now=dt_util.now(),
        )
        await self.async_refresh_summaries()
        await self.async_refresh_usage_table()
        self._async_notify()
        await self.async_evaluate_notifications()

    @property
    def cost_total_statistic_id(self) -> str | None:
        """entity_id sensor biaya grup, kalau kelompok ini memakai tarif."""
        return er.async_get(self.hass).async_get_entity_id(
            "sensor", DOMAIN, f"{self.subentry_id}_cost_total"
        )

    async def async_refresh_summaries(self) -> None:
        """Hitung ulang rincian per periode dari long-term statistics.

        Menumpang pada jadwal prediksi yang sudah ada, bukan timer sendiri:
        keduanya membaca database recorder, dan angka "bulan lalu" jelas tidak
        berubah lebih cepat dari itu.
        """
        from .engines.period_summary import (  # noqa: PLC0415
            DAYS_TO_FETCH,
            MONTHS_TO_FETCH,
            past_day_keys,
            past_month_keys,
            summarise,
        )
        from .statistics_helper import (  # noqa: PLC0415
            async_fetch_period_changes,
        )

        now = dt_util.now()
        fetched: dict[str, tuple[list, list]] = {}
        for family, statistic_id in (
            ("energy", self.energy_total_statistic_id),
            ("cost", self.cost_total_statistic_id),
        ):
            if statistic_id is None:
                continue
            fetched[family] = (
                await async_fetch_period_changes(
                    self.hass, statistic_id, "day", now - timedelta(days=DAYS_TO_FETCH)
                ),
                await async_fetch_period_changes(
                    self.hass,
                    statistic_id,
                    "month",
                    now - timedelta(days=31 * MONTHS_TO_FETCH),
                ),
            )

        # Rata-rata kWh dan rata-rata Rupiah harus dibagi jumlah periode yang
        # sama, kalau tidak Rp/kWh-nya tidak akan pernah cocok dengan tarif mana
        # pun. Statistik biaya biasanya lebih pendek - ia baru mulai tercatat
        # sejak tarif dipasang - jadi yang dipakai adalah irisan keduanya.
        only_days = only_months = None
        if len(fetched) > 1:
            only_days = set.intersection(
                *(past_day_keys(daily, now) for daily, _ in fetched.values())
            )
            only_months = set.intersection(
                *(past_month_keys(monthly, now) for _, monthly in fetched.values())
            )

        for family, (daily, monthly) in fetched.items():
            self.summaries[family] = summarise(
                daily=daily,
                monthly=monthly,
                now=now,
                only_days=only_days,
                only_months=only_months,
            )

    def summary_for(self, family: str) -> dict[str, Any]:
        """Rincian per periode satu keluarga, digabung dengan angka berjalan.

        Periode yang sedang berjalan diambil dari penghitung siklus, bukan dari
        statistik: statistik baru disusun tiap jam, sementara "hari ini" harus
        terlihat bergerak.
        """
        from .engines.period_summary import LIVE_ROWS  # noqa: PLC0415

        summary = dict(self.summaries.get(family, {}))
        if family == "energy":
            counters, total = self.counters, self.total_kwh
        else:
            counters, total = self.cost_counters, self.cost_total_rp
        for row in LIVE_ROWS:
            if (counter := counters.get(row.removeprefix("this_"))) is not None:
                summary[row] = counter.value(total)
        return summary

    async def async_evaluate_notifications(self) -> str:
        """Timbang apakah ada notifikasi token yang pantas dikirim sekarang.

        Diimpor di dalam fungsi supaya tidak ada lingkaran impor antara runtime
        dan lapisan pengiriman.
        """
        if not self.token_enabled:
            return "token_disabled"

        from .notifier import TokenNotifier  # noqa: PLC0415

        reason = await TokenNotifier(self.hass, self).async_evaluate()
        self._on_persist()
        return reason

    @callback
    def async_start_prediction_refresh(self) -> None:
        """Jadwalkan perhitungan ulang perkiraan secara berkala."""
        if self._prediction_unsub is not None:
            return
        self._prediction_unsub = async_track_time_interval(
            self.hass,
            self.async_refresh_prediction,
            timedelta(minutes=PREDICTION_REFRESH_MINUTES),
        )

    # ------------------------------------------------------------------
    # persistensi
    # ------------------------------------------------------------------

    def as_stored(self) -> dict[str, Any]:
        """Snapshot yang disimpan ke .storage."""
        return {
            "total": self.group_total.state.as_dict(),
            "counters": {
                period: counter.state.as_dict()
                for period, counter in self.counters.items()
            },
            "cost": self.cost.state.as_dict(),
            "cost_counters": {
                period: counter.state.as_dict()
                for period, counter in self.cost_counters.items()
            },
            "token": self.ledger.state.as_dict(),
            "notifier": self.notifier_state.as_dict(),
            "inputs": dict(self.inputs),
            "inputs_text": dict(self.inputs_text),
            "pending_rate": dict(self.pending_rate) if self.pending_rate else None,
        }


class PlnRuntimeData:
    """Wadah runtime untuk seluruh config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Siapkan penyimpanan dan daftar source kosong."""
        self.hass = hass
        self.entry = entry
        self.sources: dict[str, SourceRuntime] = {}
        self.billing_groups: dict[str, BillingGroupRuntime] = {}
        self._auto_purge_unsub: CALLBACK_TYPE | None = None
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}"
        )
        self._stored: dict[str, Any] = {}

    async def async_load(self) -> None:
        """Baca state akumulator yang tersimpan dari restart sebelumnya."""
        self._stored = await self._store.async_load() or {}

    @callback
    def async_setup_runtimes(self) -> None:
        """Bangun runtime untuk setiap Energy Source lalu setiap Billing Group."""
        stored_sources = self._stored.get("sources", {})
        for subentry_id, subentry in self.entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_ENERGY_SOURCE:
                continue
            runtime = SourceRuntime(
                self.hass,
                subentry_id,
                dict(subentry.data),
                stored_sources.get(subentry_id),
                self.async_schedule_save,
            )
            if not runtime.enabled:
                continue
            self.sources[subentry_id] = runtime
            runtime.async_start()

        tariffs = {
            subentry_id: dict(subentry.data)
            for subentry_id, subentry in self.entry.subentries.items()
            if subentry.subentry_type == SUBENTRY_TYPE_TARIFF
        }

        # Billing Group dibangun setelahnya karena ia berlangganan ke source.
        stored_groups = self._stored.get("billing_groups", {})
        for subentry_id, subentry in self.entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_BILLING_GROUP:
                continue
            group = BillingGroupRuntime(
                self.hass,
                subentry_id,
                dict(subentry.data),
                stored_groups.get(subentry_id),
                self.sources,
                self.async_schedule_save,
                tariffs.get(subentry.data.get(CONF_TARIFF_ID)),
            )
            # Beberapa layanan perlu menulis kembali ke config entry induk
            # (menyimpan template, menerapkan harga baru), jadi runtime perlu
            # tahu dia milik entry yang mana.
            group.entry_id = self.entry.entry_id
            self.billing_groups[subentry_id] = group
            group.async_start()

    @callback
    def async_start_auto_purge(self) -> None:
        """Jadwalkan pembersihan data otomatis, kalau user mengaktifkannya."""
        if self._auto_purge_unsub is not None:
            return
        if not self.entry.options.get(
            CONF_AUTO_PURGE_ENABLED, DEFAULT_AUTO_PURGE_ENABLED
        ):
            return
        self._auto_purge_unsub = async_track_time_interval(
            self.hass,
            self._async_auto_purge,
            timedelta(hours=AUTO_PURGE_INTERVAL_HOURS),
        )

    async def _async_auto_purge(self, _now: datetime) -> None:
        """Bersihkan statistik lama tanpa diminta, sesuai retensi yang dipilih."""
        from .retention import (  # noqa: PLC0415
            RetentionUnsupportedError,
            async_purge_statistics,
            retention_days,
        )
        from .services import our_statistic_ids  # noqa: PLC0415

        days = retention_days(
            self.entry.options.get(
                CONF_STATISTICS_RETENTION_YEARS, DEFAULT_STATISTICS_RETENTION_YEARS
            )
        )
        if days is None:
            return

        cutoff = dt_util.utcnow() - timedelta(days=days)
        try:
            await async_purge_statistics(
                self.hass, our_statistic_ids(self.hass), cutoff
            )
        except RetentionUnsupportedError:
            # Sekali gagal berarti akan gagal terus sampai HA diperbarui lagi;
            # matikan jadwalnya supaya tidak membanjiri log tiap hari.
            _LOGGER.exception(
                "Pembersihan otomatis dimatikan karena struktur recorder tidak dikenali"
            )
            if self._auto_purge_unsub is not None:
                self._auto_purge_unsub()
                self._auto_purge_unsub = None

    async def async_start_predictions(self) -> None:
        """Hitung perkiraan pertama, lalu jadwalkan pembaruan berkala.

        Dipanggil setelah platform entity siap, karena pembacaan statistik butuh
        entity_id sensor energi grup yang baru terdaftar di situ.
        """
        for group in self.billing_groups.values():
            await group.async_refresh_prediction()
            group.async_start_prediction_refresh()

    @callback
    def async_schedule_save(self) -> None:
        """Simpan state akumulator, ditunda supaya tidak menulis terlalu sering."""
        self._store.async_delay_save(self._data_to_save, STORAGE_SAVE_DELAY_SECONDS)

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        """Bentuk data yang akan ditulis ke .storage."""
        return {
            "sources": {
                subentry_id: runtime.as_stored()
                for subentry_id, runtime in self.sources.items()
            },
            "billing_groups": {
                subentry_id: group.as_stored()
                for subentry_id, group in self.billing_groups.items()
            },
        }

    async def async_shutdown(self) -> None:
        """Hentikan semua runtime dan tulis state terakhir tanpa ditunda."""
        if self._auto_purge_unsub is not None:
            self._auto_purge_unsub()
            self._auto_purge_unsub = None
        for group in self.billing_groups.values():
            group.async_stop()
        for runtime in self.sources.values():
            runtime.async_stop()
        await self._store.async_save(self._data_to_save())
        self.billing_groups.clear()
        self.sources.clear()
