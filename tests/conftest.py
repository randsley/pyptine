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
def no_retry_waits(request, monkeypatch):
    """Retry without waiting (the real waits are 5, 20 and 60 s). Tests of the waits
    themselves pass retry_delays explicitly or patch time.sleep. Live tests keep the real
    waits: INE answers bursts with 404, and only waiting gets past it."""
    if request.node.get_closest_marker("live"):
        return
    from pyptine.client.async_base import AsyncINEClient
    from pyptine.client.base import INEClient

    monkeypatch.setattr(INEClient, "RETRY_DELAYS", (0.0, 0.0, 0.0))
    monkeypatch.setattr(AsyncINEClient, "RETRY_DELAYS", (0.0, 0.0, 0.0))


@pytest.fixture
def fixtures_dir() -> Path:
    """INE's recorded responses (see tests/fixtures/ine/PROVENANCE.json)."""
    return Path(__file__).parent / "fixtures" / "ine"


@pytest.fixture
def sample_metadata(fixtures_dir: Path) -> list[dict[str, Any]]:
    """INE's metadata for 0008273 (resident population, NUTS 2013), EN, as recorded."""
    return json.loads((fixtures_dir / "meta_0008273_EN.json").read_bytes())


@pytest.fixture
def sample_data(fixtures_dir: Path) -> list[dict[str, Any]]:
    """INE's data for 0008273, 2021 and 2022, Portugal, both sexes, all ages, EN, as recorded
    (query: Dim1=S7A2021,S7A2022 Dim2=PT Dim3=T Dim4=T)."""
    return json.loads((fixtures_dir / "data_0008273_S7A2021_S7A2022_EN.json").read_bytes())


@pytest.fixture
def sample_catalogue(fixtures_dir: Path) -> str:
    """INE's catalogue main group (opc=3, 309 indicators), EN, as recorded. It stands in for
    the whole catalogue (opc=2, 21 MB), which has the same XML shape; titles are Portuguese."""
    return (fixtures_dir / "catalogue_opc3_EN.xml").read_text(encoding="utf-8")


@pytest.fixture
def temp_cache_dir(tmp_path: Path) -> Path:
    """Create temporary cache directory for tests."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(exist_ok=True)
    return cache_dir
