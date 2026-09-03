"""PLN Prepaid Energy & Cost Monitor.

Sistem ini HANYA memantau, menghitung, memprediksi, dan memberi notifikasi.
Ia tidak pernah mendaftarkan platform ``switch``/``number``/``select`` dan
tidak pernah memanggil service apa pun yang bisa memutus atau menyalakan
listrik - lihat ``const.PLATFORMS`` dan test ``test_readonly_guarantee.py``.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .coordinator import PlnRuntimeData

type PlnConfigEntry = ConfigEntry[PlnRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: PlnConfigEntry) -> bool:
    """Siapkan entry: pulihkan state akumulator lalu jalankan semua source."""
    runtime_data = PlnRuntimeData(hass, entry)
    await runtime_data.async_load()
    runtime_data.async_setup_runtimes()
    entry.runtime_data = runtime_data

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Menambah, mengedit, atau menghapus Energy Source memicu update pada
    # config entry induk. Listener ini yang membuat perubahan itu langsung
    # berlaku tanpa perlu restart Home Assistant.
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PlnConfigEntry) -> bool:
    """Hentikan semua source dan simpan state terakhir."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: PlnConfigEntry) -> None:
    """Muat ulang entry setelah konfigurasi berubah."""
    await hass.config_entries.async_reload(entry.entry_id)
