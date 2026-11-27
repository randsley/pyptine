"""Live tests: what users do, end to end, against INE's API.

Deselected by default; run with ``pytest -m live`` (weekly in CI, ``live.yml``). The offline
suite pins parsing against recorded responses and ``tools/check_api.py`` watches the fields;
these check that the whole path still works on today's INE.

They assert codes and structure, never figures: INE revises its data, and a revision is not
a break. Facts pinned here were verified on 2026-11-27 (see CLAUDE.md, "INE API quirks").
Requests are kept few and sequential: INE throttles bursts (404), which the client retries
after 5, 20 and 60 s.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from pyptine import INE, AsyncINE
from pyptine.utils.exceptions import DimensionError, INERefusalError

pytestmark = pytest.mark.live

TOTAL = {"Dim2": "PT", "Dim3": "T", "Dim4": "T"}  # 0008273: Portugal, both sexes, all ages


@pytest.fixture(scope="module")
def ine():
    return INE(language="EN", cache=False)


def test_periods_and_provenance(ine):
    r = ine.get_data("0008273", {"Dim1": ["S7A2021", "S7A2022"], **TOTAL})

    assert r.period_codes == ["S7A2021", "S7A2022"]
    assert r.periods == ["2021", "2022"]
    assert all(row["value"] > 0 for row in r.data)
    # INE's own timestamp, in UTC: it was taken just now.
    age = datetime.now(timezone.utc) - r.extraction_date
    assert timedelta(minutes=-5) < age < timedelta(hours=1)
    assert "Dim1=S7A2021,S7A2022" in r.url
    assert "indicator 0008273" in r.citation


def test_latest_period_only_without_dim1(ine):
    latest = [v.code for v in ine.get_dimensions("0008273")[0].values][-1]
    r = ine.get_data("0008273", TOTAL)
    assert r.period_codes == [latest]


def test_every_level_in_one_request(ine):
    r = ine.get_data("0008273", {"Dim1": "S7A2023", "Dim3": "T", "Dim4": "T"})

    assert r.geo_classification == "nuts"
    levels = Counter(row["geo_level"] for row in r.with_geo_level().data)
    assert levels == {"municipality": 308, "nuts3": 25, "nuts2": 7, "nuts1": 3, "country": 1}
    municipalities = sum(row["value"] for row in r.at_level("municipality").data)
    assert municipalities == r.at_level("country").data[0]["value"]


def test_districts(ine):
    """0004167 codes its geography by district: 11 is Lisboa's district, not NUTS II Norte."""
    r = ine.get_data("0004167", {"Dim3": "T", "Dim4": "T"})

    assert r.geo_classification == "district"
    districts = r.at_level("district").data
    assert len(districts) == 18
    assert {row["geocod"]: row["geodsg"] for row in districts}["11"] == "Lisboa"
    mainland = next(row for row in r.at_level("nuts1").data if row["geocod"] == "1")
    assert sum(row["value"] for row in districts) == mainland["value"]


def test_quarterly_series(ine):
    codes = [v.code for v in ine.get_dimensions("0012234")[0].values]
    assert codes == sorted(codes)  # codes sort chronologically; labels do not

    r = ine.get_data("0012234", {"Dim1": codes[-5:], "Dim2": "PT", "Dim3": "H1"})
    growth = r.calculate_yoy_growth().data
    assert len(growth) == 5
    assert growth[-1]["yoy_growth"] is not None  # the same quarter a year before is there


def test_wrong_codes_are_refused(ine):
    with pytest.raises(DimensionError, match="use 'H1'"):
        ine.get_data("0012234", {"Dim2": "PT", "Dim3": "H.1"})  # categ_cod, not cat_id
    with pytest.raises(DimensionError, match="use the code 'S7A2022'"):
        ine.get_data("0008273", {"Dim1": "2022"})  # the label, not the code
    with pytest.raises(INERefusalError) as e:
        ine.get_data("9999999")
    assert e.value.ine_code == "1"


def test_catalogue(ine):
    metadata = ine.get_metadata("0008273")  # fills theme, source, ... from opc=1
    assert metadata.theme == "Population"
    assert metadata.source
    assert ine.validate_indicator("0008273")
    assert not ine.validate_indicator("9999999")  # INE's Java error page, refused at once


def test_search_whole_catalogue(ine):
    """Search reads opc=2: every indicator (13,290 on 2026-11-27, 21 MB, ~20 s)."""
    indicators = ine.browser.get_all_indicators()
    assert len(indicators) > 10_000
    assert "0008273" in {i.varcd for i in indicators}
    assert any(i.varcd == "0008273" for i in ine.search("resident population"))


def test_export_with_provenance(ine, tmp_path):
    r = ine.get_data("0008273", {"Dim1": "S7A2023", **TOTAL})
    path = tmp_path / "population.csv"
    r.to_csv(path)
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    header = "\n".join(line for line in lines if line.startswith("#"))
    assert "# citation: Source: Statistics Portugal (INE), indicator 0008273" in header
    assert "# ine_extraction_date: " in header


async def test_async_matches_sync(ine):
    query = {"Dim1": ["S7A2021", "S7A2022"], **TOTAL}
    sync = ine.get_data("0008273", query)
    async with AsyncINE(language="EN") as aine:
        asynchronous = await aine.get_data("0008273", query)
    assert asynchronous.data == sync.data
