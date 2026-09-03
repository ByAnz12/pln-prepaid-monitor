"""Runtime per Energy Source: mendengarkan sumber, menormalkan, mengakumulasi.

Ini bukan ``DataUpdateCoordinator`` bergaya polling, karena tidak ada apa pun
yang perlu di-poll: seluruh data datang dari entity lain di Home Assistant yang
sudah punya mekanisme update sendiri. Kita berlangganan perubahan state
(event-driven) supaya tidak menambah beban dan tidak memperlambat pembacaan.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
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
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CHANNEL_CONF_KEYS,
    CHANNEL_CURRENT,
    CHANNEL_ENERGY,
    CHANNEL_FREQUENCY,
    CHANNEL_POWER,
    CHANNEL_VOLTAGE,
    CONF_AVAILABILITY_ENTITY_ID,
    CONF_ENABLED,
    CONF_UNAVAILABLE_GRACE_MINUTES,
    DEFAULT_UNAVAILABLE_GRACE_MINUTES,
    SOURCE_OF_TRUTH_CUMULATIVE,
    SOURCE_OF_TRUTH_INTEGRATED,
    STORAGE_KEY,
    STORAGE_SAVE_DELAY_SECONDS,
    STORAGE_VERSION,
)
from .engines.accumulator import (
    AccumulatorEvent,
    AccumulatorState,
    IntegratorState,
    PowerIntegrator,
    ResetSafeAccumulator,
)
from .engines.normalization import CHANNEL_SPECS, conversion_factor

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


class PlnRuntimeData:
    """Wadah runtime untuk seluruh config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Siapkan penyimpanan dan daftar source kosong."""
        self.hass = hass
        self.entry = entry
        self.sources: dict[str, SourceRuntime] = {}
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}"
        )
        self._stored: dict[str, Any] = {}

    async def async_load(self) -> None:
        """Baca state akumulator yang tersimpan dari restart sebelumnya."""
        self._stored = await self._store.async_load() or {}

    @callback
    def async_setup_sources(self) -> None:
        """Bangun runtime untuk setiap Energy Source yang aktif."""
        sources = self._stored.get("sources", {})
        for subentry_id, subentry in self.entry.subentries.items():
            runtime = SourceRuntime(
                self.hass,
                subentry_id,
                dict(subentry.data),
                sources.get(subentry_id),
                self.async_schedule_save,
            )
            if not runtime.enabled:
                continue
            self.sources[subentry_id] = runtime
            runtime.async_start()

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
            }
        }

    async def async_shutdown(self) -> None:
        """Hentikan semua runtime dan tulis state terakhir tanpa ditunda."""
        for runtime in self.sources.values():
            runtime.async_stop()
        await self._store.async_save(self._data_to_save())
        self.sources.clear()
