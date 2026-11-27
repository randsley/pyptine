"""Pytest configuration and shared fixtures."""

import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def isolated_cache_dir(monkeypatch, tmp_path_factory):
    """Point pyptine's default cache directory at a temporary one. Without this, code that
    builds INE(cache=True) (the CLI does) reads and writes the user's real cache: the tests
    then depend on what is cached, and leave hand-written responses where real use finds
    them."""
    import pyptine.cache.disk as disk

    cache = tmp_path_factory.mktemp("pyptine-cache")
    monkeypatch.setattr(disk, "user_cache_dir", lambda *args, **kwargs: str(cache))


@pytest.fixture(autouse=True)
def no_retry_waits(monkeypatch):
    """Retry without waiting (the real waits are 5, 20 and 60 s). Tests of the waits
    themselves pass retry_delays explicitly or patch time.sleep."""
    from pyptine.client.async_base import AsyncINEClient
    from pyptine.client.base import INEClient

    monkeypatch.setattr(INEClient, "RETRY_DELAYS", (0.0, 0.0, 0.0))
    monkeypatch.setattr(AsyncINEClient, "RETRY_DELAYS", (0.0, 0.0, 0.0))


@pytest.fixture
def fixtures_dir() -> Path:
    """Return path to fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_metadata(fixtures_dir: Path) -> dict[str, Any]:
    """Load sample metadata response."""
    fixture_path = fixtures_dir / "metadata_response.json"
    if fixture_path.exists():
        with open(fixture_path) as f:
            return json.load(f)

    # Return minimal sample if fixture doesn't exist yet
    return {
        "indicador": "0004167",
        "nome": "Resident population",
        "lang": "EN",
        "dimensoes": [
            {
                "id": 1,
                "nome": "Period",
                "valores": [
                    {"codigo": "2020", "label": "2020"},
                    {"codigo": "2021", "label": "2021"},
                ],
            }
        ],
    }


@pytest.fixture
def sample_data(fixtures_dir: Path) -> dict[str, Any]:
    """Load sample data response."""
    fixture_path = fixtures_dir / "data_response.json"
    if fixture_path.exists():
        with open(fixture_path) as f:
            return json.load(f)

    # Return minimal sample if fixture doesn't exist yet
    return {
        "indicador": "0004167",
        "nome": "Resident population",
        "lang": "EN",
        "dados": [
            {"periodo": "2020", "geo": "Portugal", "valor": "10298252"},
            {"periodo": "2021", "geo": "Portugal", "valor": "10295909"},
        ],
    }


@pytest.fixture
def sample_catalogue(fixtures_dir: Path) -> str:
    """Load sample catalogue XML response."""
    fixture_path = fixtures_dir / "catalogue_response.xml"
    if fixture_path.exists():
        with open(fixture_path) as f:
            return f.read()

    # Return minimal sample if fixture doesn't exist yet
    return """<?xml version="1.0" encoding="UTF-8"?>
<catalog>
    <indicator id="0004167">
        <theme>Population</theme>
        <subtheme>Demographic estimates</subtheme>
        <keywords>INE,population,residents,demographics</keywords>
        <title>Resident population</title>
        <varcd>0004167</varcd>
        <description>Resident population (No.) by Place of residence (NUTS - 2013); Annual</description>
        <geo_lastlevel>Municipality</geo_lastlevel>
        <source>INE, Population estimates</source>
        <dates>
            <last_period_available>2023</last_period_available>
            <last_update>14-06-2024</last_update>
        </dates>
        <periodicity>Annual</periodicity>
        <html>
            <bdd_url>https://www.ine.pt/xurl/indx/0004167/EN</bdd_url>
            <metainfo_url>https://www.ine.pt/xurl/metax/0004167/EN</metainfo_url>
        </html>
        <json>
            <json_dataset>https://www.ine.pt/ine/json_indicador/pindica.jsp?op=2&amp;varcd=0004167&amp;lang=EN</json_dataset>
            <json_metainfo>https://www.ine.pt/ine/json_indicador/pindicaMeta.jsp?varcd=0004167&amp;lang=EN</json_metainfo>
        </json>
    </indicator>
    <indicator id="0008074">
        <theme>Population</theme>
        <subtheme>Demographic estimates</subtheme>
        <keywords>INE,population,sex,age</keywords>
        <title>Resident population by sex and age group</title>
        <varcd>0008074</varcd>
        <description>Resident population by sex and age group; Annual</description>
        <geo_lastlevel>National</geo_lastlevel>
        <source>INE, Population estimates</source>
        <dates>
            <last_period_available>2023</last_period_available>
            <last_update>14-06-2024</last_update>
        </dates>
        <periodicity>Annual</periodicity>
        <html>
            <bdd_url>https://www.ine.pt/xurl/indx/0008074/EN</bdd_url>
            <metainfo_url>https://www.ine.pt/xurl/metax/0008074/EN</metainfo_url>
        </html>
        <json>
            <json_dataset>https://www.ine.pt/ine/json_indicador/pindica.jsp?op=2&amp;varcd=0008074&amp;lang=EN</json_dataset>
            <json_metainfo>https://www.ine.pt/ine/json_indicador/pindicaMeta.jsp?varcd=0008074&amp;lang=EN</json_metainfo>
        </json>
    </indicator>
</catalog>
"""


@pytest.fixture
def temp_cache_dir(tmp_path: Path) -> Path:
    """Create temporary cache directory for tests."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(exist_ok=True)
    return cache_dir


@pytest.fixture
def mock_ine_client():
    """Create a mock INE client for testing."""
    from pyine.client.base import INEClient

    client = INEClient(language="EN", cache_enabled=False)
    yield client
    client.close()
