"""Aturan hassfest, diperiksa di sini supaya ikut gagal di pytest.

hassfest milik tim Home Assistant memeriksa `services.yaml`, `strings.json`,
dan `translations/` terhadap aturan yang tidak disentuh test lain sama sekali.
Ia hanya berjalan di workflow **Validate**, dan `release.yml` dulu tidak
menunggunya - jadi hassfest sempat merah di `main` selama empat commit tanpa
ada yang sadar, dan rilis 0.3.0 tetap terbit di atasnya (lihat D-053).

Yang bikin kelas kesalahan ini berbahaya: **tidak satu pun berbentuk error saat
integrasinya dipakai**. `description` pada entity cuma diam-diam tidak pernah
tampil, kunci state `-` cuma membuat seluruh berkas terjemahan ditolak diam-
diam, dan filter perangkat pada `target` cuma membuat pemilih di UI menawarkan
area yang skemanya sendiri tolak. Semuanya terlihat baik-baik saja.

Aturannya disalin dari sumbernya, bukan dikarang:

* `script/hassfest/services.py` - `raise_on_target_device_filter`
* `script/hassfest/translations.py` - skema `entity`, `translation_key_validator`,
  `RE_PLACEHOLDER_IN_SINGLE_QUOTES`
* `homeassistant/helpers/selector.py` - dipakai langsung, bukan disalin
* `homeassistant/helpers/entity.py` - kunci terjemahan yang benar-benar dibaca

Satu test tambahan yang bukan milik hassfest: `id` dan `en` harus punya kunci
yang persis sama. hassfest hanya melihat `strings.json` dan `en.json`, jadi
kunci yang tertinggal di `id.json` lolos begitu saja dan muncul sebagai teks
Inggris di tengah antarmuka berbahasa Indonesia.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

import pytest
import yaml

from homeassistant.helpers import selector

from custom_components.pln_prepaid_monitor.select import NONE_OPTION

COMPONENT = pathlib.Path("custom_components/pln_prepaid_monitor")

SERVICES: dict[str, Any] = yaml.safe_load(
    (COMPONENT / "services.yaml").read_text(encoding="utf-8")
)
STRINGS: dict[str, Any] = json.loads(
    (COMPONENT / "strings.json").read_text(encoding="utf-8")
)
TRANSLATIONS = {
    path.stem: json.loads(path.read_text(encoding="utf-8"))
    for path in sorted((COMPONENT / "translations").glob("*.json"))
}

ALL_TRANSLATION_FILES = {"strings": STRINGS, **TRANSLATIONS}

# script/hassfest/translations.py: kunci terjemahan harus slug, dan tidak boleh
# diawali atau diakhiri tanda hubung maupun garis bawah.
RE_TRANSLATION_KEY = re.compile(r"^(?!.*[_-]$)(?![_-])[a-z0-9-_]+$")

# script/hassfest/translations.py: RE_PLACEHOLDER_IN_SINGLE_QUOTES.
RE_PLACEHOLDER_IN_SINGLE_QUOTES = re.compile(r"'{\w+}'")

# script/hassfest/translations.py, skema di bawah kunci `entity`. Tidak ada
# `description` di sini - dan helpers/entity.py memang tidak pernah mencarinya.
ENTITY_TRANSLATION_KEYS = {"name", "state", "state_attributes", "unit_of_measurement"}


def _walk(value: Any, path: str = "") -> list[tuple[str, str]]:
    """Setiap teks di dalam berkas terjemahan, beserta jalurnya."""
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        return [
            item
            for key, child in value.items()
            for item in _walk(child, f"{path}.{key}" if path else key)
        ]
    if isinstance(value, list):
        return [
            item
            for index, child in enumerate(value)
            for item in _walk(child, f"{path}[{index}]")
        ]
    return []


def _key_paths(value: Any, path: str = "") -> set[str]:
    """Jalur setiap kunci, tanpa isinya - untuk membandingkan dua bahasa."""
    if not isinstance(value, dict):
        return {path}
    return {
        item
        for key, child in value.items()
        for item in _key_paths(child, f"{path}.{key}" if path else key)
    }


# --- services.yaml ----------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_no_device_filter_on_target(name: str) -> None:
    """Filter perangkat pada `target` ditolak hassfest.

    Bukan cuma soal lulus pemeriksaan: pemilih `target` di UI juga menawarkan
    area dan entity, padahal seluruh layanan di sini hanya membaca `device_id`.
    Memilih area menghasilkan penolakan skema yang tidak menjelaskan apa pun.
    Device selector pada sebuah field menawarkan persis yang diterima kode.
    """
    target = (SERVICES[name] or {}).get("target") or {}
    assert "device" not in target


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_every_selector_is_valid(name: str) -> None:
    """Divalidasi lewat selector Home Assistant sendiri, bukan daftar salinan."""
    for field in ((SERVICES[name] or {}).get("fields") or {}).values():
        if "selector" in field:
            selector.validate_selector(field["selector"])


@pytest.mark.parametrize("language", sorted(ALL_TRANSLATION_FILES))
def test_every_service_field_is_explained(language: str) -> None:
    """Setiap isian punya nama dan penjelasan awam, di kedua bahasa.

    Ini lebih ketat daripada hassfest, yang hanya menuntutnya untuk integrasi
    bawaan. Permintaan pemilik sejak awal: bukan sekadar nama.
    """
    services = ALL_TRANSLATION_FILES[language]["services"]
    for name, schema in SERVICES.items():
        for field in ((schema or {}).get("fields") or {}):
            texts = services[name]["fields"][field]
            assert texts["name"], f"{language}: {name}.{field} tanpa nama"
            assert texts["description"], f"{language}: {name}.{field} tanpa penjelasan"


@pytest.mark.parametrize("language", sorted(ALL_TRANSLATION_FILES))
def test_no_leftover_field_translations(language: str) -> None:
    """Isian yang sudah dihapus dari services.yaml tidak boleh tertinggal."""
    services = ALL_TRANSLATION_FILES[language]["services"]
    for name, schema in SERVICES.items():
        declared = set(((schema or {}).get("fields") or {}))
        translated = set(services[name].get("fields") or {})
        assert translated <= declared, f"{language}: {name} punya isian hantu"


# --- strings.json dan translations/ -----------------------------------------


@pytest.mark.parametrize("language", sorted(ALL_TRANSLATION_FILES))
def test_entity_translations_only_use_keys_home_assistant_reads(language: str) -> None:
    """`description` pada entity tidak pernah dibaca Home Assistant.

    `helpers/entity.py` hanya mencari `.name`; `state`, `state_attributes`, dan
    `unit_of_measurement` dibaca di tempat lain. Kunci di luar itu adalah teks
    yang ditulis untuk pengguna tapi tidak pernah sampai ke layar - kekeliruan
    yang tidak akan pernah terlihat kalau tidak dijaga di sini.
    """
    for domain, entities in ALL_TRANSLATION_FILES[language].get("entity", {}).items():
        for key, texts in entities.items():
            extra = set(texts) - ENTITY_TRANSLATION_KEYS
            assert not extra, f"{language}: entity.{domain}.{key} punya {extra}"


@pytest.mark.parametrize("language", sorted(ALL_TRANSLATION_FILES))
def test_entity_state_keys_are_slugs(language: str) -> None:
    """Kunci state harus slug - `-` menolak seluruh berkasnya sekaligus."""
    for domain, entities in ALL_TRANSLATION_FILES[language].get("entity", {}).items():
        for key, texts in entities.items():
            for state in texts.get("state", {}):
                assert RE_TRANSLATION_KEY.match(state), (
                    f"{language}: entity.{domain}.{key}.state.{state}"
                )


def test_the_empty_template_option_has_a_translation() -> None:
    """Nilai kosong pemilih template harus punya kalimatnya di tiap bahasa.

    Kalau tidak, yang muncul di layar adalah nilai mentahnya - dan sejak nilai
    itu menjadi slug, mentahnya terbaca sebagai kode, bukan kalimat.
    """
    assert RE_TRANSLATION_KEY.match(NONE_OPTION)
    for language, data in ALL_TRANSLATION_FILES.items():
        states = data["entity"]["select"]["topup_template"]["state"]
        assert NONE_OPTION in states, f"{language}: {NONE_OPTION} tidak diterjemahkan"


@pytest.mark.parametrize("language", sorted(ALL_TRANSLATION_FILES))
def test_no_placeholder_inside_single_quotes(language: str) -> None:
    """Petik tunggal di sekitar placeholder ditolak; HA core memakai petik ganda."""
    for path, text in _walk(ALL_TRANSLATION_FILES[language]):
        assert not RE_PLACEHOLDER_IN_SINGLE_QUOTES.search(text), f"{language}: {path}"


@pytest.mark.parametrize("language", sorted(TRANSLATIONS))
def test_both_languages_have_the_same_keys(language: str) -> None:
    """Kunci yang tertinggal muncul sebagai bahasa Inggris di tengah bahasa Indonesia.

    hassfest tidak menangkap ini: ia hanya melihat `strings.json` dan `en.json`.
    """
    missing = _key_paths(STRINGS) - _key_paths(TRANSLATIONS[language])
    extra = _key_paths(TRANSLATIONS[language]) - _key_paths(STRINGS)
    assert not missing, f"{language}.json kurang: {sorted(missing)[:5]}"
    assert not extra, f"{language}.json kelebihan: {sorted(extra)[:5]}"
