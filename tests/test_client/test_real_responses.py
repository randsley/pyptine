"""Tests pinned against real INE responses (tests/fixtures/ine/, see PROVENANCE.json).

Each fixture is served for exactly the query it was recorded with, so these tests also pin the
URLs pyptine builds. Recorded 2026-11-27 by tools/capture_fixtures.py.
"""

import json
import warnings
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
import pytest
import responses
from responses import matchers

from pyptine.analysis.metrics import calculate_yoy_growth
from pyptine.client.async_data import AsyncDataClient
from pyptine.client.data import DataClient
from pyptine.client.metadata import MetadataClient
from pyptine.client.parsing import check_success, is_refusal_body, period_codes
from pyptine.models.indicator import Indicator
from pyptine.utils.exceptions import DimensionError, INERefusalError

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ine"
PROVENANCE = json.loads((FIXTURES / "PROVENANCE.json").read_text(encoding="utf-8"))["files"]
TOTAL = {"Dim2": "PT", "Dim3": "T", "Dim4": "T"}  # 0008273: Portugal, both sexes, all ages


def recorded(name: str) -> tuple[str, dict[str, str], bytes]:
    """URL without query, query parameters, and body of a recorded response."""
    base, _, query = PROVENANCE[name]["url"].partition("?")
    return base, dict(parse_qsl(query)), (FIXTURES / name).read_bytes()


def serve(rsps: responses.RequestsMock, *names: str) -> None:
    """Serve recorded responses, each only for the exact query it was recorded with."""
    for name in names:
        base, params, body = recorded(name)
        rsps.add(
            responses.GET,
            base,
            body=body,
            content_type="application/json;charset=UTF-8",
            match=[matchers.query_param_matcher(params)],
        )


@pytest.fixture
def rsps():
    with responses.RequestsMock(assert_all_requests_are_fired=True) as mock:
        yield mock


def client(language: str = "EN") -> DataClient:
    return DataClient(language=language, cache_enabled=False)


def test_fixtures_unchanged():
    """The recorded bytes match PROVENANCE.json (git must not rewrite them)."""
    import hashlib

    for name, info in PROVENANCE.items():
        assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == info["sha256"], name


class TestPeriods:
    """Finding 1 and 4: rows carry the period; several periods can be requested."""

    def test_latest_period_only_without_dim1(self, rsps):
        serve(rsps, "meta_0008273_EN.json", "data_0008273_latest_EN.json")
        r = client().get_data("0008273", TOTAL)
        assert len(r.data) == 1
        row = r.data[0]
        assert (row["period"], row["period_code"]) == ("2023", "S7A2023")
        assert row["geocod"] == "PT"
        assert row["value"] == 11204347.0
        assert row["ind_string"] == "11 204 347"  # display form kept, never parsed
        assert r.unit == "Number (No.)"  # from the metadata: the data response has no unit

    @pytest.mark.parametrize("dim1", [["S7A2021", "S7A2022"], "S7A2021,S7A2022"])
    def test_several_periods(self, rsps, dim1):
        serve(rsps, "meta_0008273_EN.json", "data_0008273_S7A2021_S7A2022_EN.json")
        r = client().get_data("0008273", {"Dim1": dim1, **TOTAL})
        got = [(row["period_code"], row["period"], row["value"]) for row in r.data]
        assert got == [("S7A2021", "2021", 10599117.0), ("S7A2022", "2022", 10929704.0)]
        df = r.to_dataframe()
        assert list(df.columns[:2]) == ["period", "period_code"]

    def test_several_periods_portuguese(self, rsps):
        serve(rsps, "meta_0008273_PT.json", "data_0008273_S7A2021_S7A2022_PT.json")
        r = client("PT").get_data("0008273", {"Dim1": ["S7A2021", "S7A2022"], **TOTAL})
        assert [row["period_code"] for row in r.data] == ["S7A2021", "S7A2022"]
        assert r.data[0]["dim_3_t"] == "HM"  # Portuguese label for both sexes (EN: "MF")

    def test_quarterly_label(self, rsps):
        serve(rsps, "meta_0012234_EN.json", "data_0012234_H1_EN.json")
        r = client().get_data("0012234", {"Dim1": "S5A20261", "Dim2": "PT", "Dim3": "H1"})
        assert [(row["period"], row["period_code"]) for row in r.data] == [
            ("1st Quarter 2026", "S5A20261")
        ]

    def test_unknown_period_label_is_refused(self):
        meta = MetadataClient(cache_enabled=False)._parse_metadata_response(
            json.loads((FIXTURES / "meta_0008273_EN.json").read_bytes())
        )
        body = json.loads((FIXTURES / "data_0008273_latest_EN.json").read_bytes())
        body[0]["Dados"] = {"2099": body[0]["Dados"]["2023"]}
        from pyptine.client.parsing import parse_data_response
        from pyptine.utils.exceptions import DataProcessingError

        with pytest.raises(DataProcessingError, match="'2099'"):
            parse_data_response("0008273", body, "EN", lambda: meta)

    def test_every_geographic_level_in_one_response(self, rsps):
        serve(rsps, "meta_0008273_EN.json", "data_0008273_S7A2022_levels_EN.json")
        r = client().get_data("0008273", {"Dim1": "S7A2022", "Dim3": "T", "Dim4": "T"})
        lengths: dict[int, int] = {}
        for row in r.data:
            lengths[len(row["geocod"])] = lengths.get(len(row["geocod"]), 0) + 1
        # country and NUTS II (2), NUTS I (1), NUTS III (3), municipalities (7)
        assert lengths == {7: 308, 3: 25, 2: 8, 1: 3}
        assert {row["period_code"] for row in r.data} == {"S7A2022"}


class TestRefusals:
    """Finding 2: INE refuses with HTTP 200 and Sucesso.Falso; pyptine raises."""

    def test_unknown_indicator_data(self, rsps):
        serve(rsps, "data_9999999_EN.json")
        with pytest.raises(INERefusalError) as e:
            client().get_data("9999999")
        assert e.value.ine_code == "1"
        assert "does not exist" in e.value.ine_message
        assert e.value.varcd == "9999999"

    def test_unknown_indicator_metadata(self, rsps):
        serve(rsps, "meta_9999999_EN.json")
        with pytest.raises(INERefusalError) as e:
            MetadataClient(cache_enabled=False).get_metadata("9999999")
        assert e.value.ine_code == "1"

    def test_period_label_refused_by_ine(self):
        body = json.loads((FIXTURES / "data_0008273_Dim1label_EN.json").read_bytes())
        with pytest.raises(INERefusalError) as e:
            check_success(body, "0008273")
        assert e.value.ine_code == "4"

    def test_refusal_not_cached(self, rsps, tmp_path):
        serve(rsps, "data_9999999_EN.json")
        c = DataClient(cache_enabled=True, cache_dir=tmp_path)
        for _ in range(2):
            with pytest.raises(INERefusalError):
                c.get_data("9999999")
        assert len(rsps.calls) == 2  # asked INE twice: the refusal was not cached
        c.close()

    def test_success_is_cached(self, rsps, tmp_path):
        serve(rsps, "meta_0008273_EN.json")
        m = MetadataClient(cache_enabled=True, cache_dir=tmp_path)
        m.get_metadata("0008273")
        m.get_metadata("0008273")
        assert len(rsps.calls) == 1
        m.close()

    def test_refusal_body_detection(self):
        assert is_refusal_body((FIXTURES / "data_9999999_EN.json").read_bytes())
        assert is_refusal_body((FIXTURES / "data_0012234_Hdot1_EN.json").read_bytes())
        assert not is_refusal_body((FIXTURES / "data_0008273_latest_EN.json").read_bytes())
        assert not is_refusal_body((FIXTURES / "meta_0008273_EN.json").read_bytes())

    def test_cache_filter_matches(self):
        from types import SimpleNamespace

        from pyptine.cache.disk import _cacheable

        for name in PROVENANCE:
            if name.endswith(".json"):
                body = (FIXTURES / name).read_bytes()
                assert _cacheable(SimpleNamespace(content=body)) is not is_refusal_body(body)


class TestValidation:
    """Finding 5: filters take cat_id; the message names the right code."""

    def test_cat_id_is_the_code(self, rsps):
        serve(rsps, "meta_0012234_EN.json")
        dims = MetadataClient(cache_enabled=False).get_dimensions("0012234")
        h1 = next(v for v in dims[2].values if v.code == "H1")
        assert h1.categ_cod == "H.1"

    def test_cat_id_accepted(self, rsps):
        serve(rsps, "meta_0012234_EN.json")
        assert client().validate_dimensions("0012234", {"Dim3": "H1"})

    def test_categ_cod_refused_with_hint(self, rsps):
        serve(rsps, "meta_0012234_EN.json")
        with pytest.raises(DimensionError, match="categ_cod of 'H1'.*use 'H1'"):
            client().validate_dimensions("0012234", {"Dim3": "H.1"})

    def test_period_label_refused_with_hint(self, rsps):
        serve(rsps, "meta_0008273_EN.json")
        with pytest.raises(DimensionError, match="label of 'S7A2022'"):
            client().get_data("0008273", {"Dim1": "2022"})

    def test_unknown_key(self, rsps):
        serve(rsps, "meta_0008273_EN.json")
        with pytest.raises(DimensionError, match="Invalid dimension key 'Dim5'"):
            client().validate_dimensions("0008273", {"Dim5": "T"})

    def test_empty_code(self, rsps):
        serve(rsps, "meta_0008273_EN.json")
        with pytest.raises(DimensionError, match="Empty code"):
            client().validate_dimensions("0008273", {"Dim1": "S7A2021,"})

    def test_metadata_language_must_match(self):
        with pytest.raises(ValueError, match="language"):
            DataClient(language="EN", metadata_client=MetadataClient("PT", cache_enabled=False))


class TestGetAllData:
    """Finding 3: INE ignores start/count; get_all_data batches periods instead."""

    def test_ine_ignores_count(self):
        body = json.loads((FIXTURES / "data_0008273_count1_EN.json").read_bytes())
        assert sum(len(rows) for rows in body[0]["Dados"].values()) == 2  # count=1 asked

    def test_period_batches_oldest_first(self, rsps):
        serve(rsps, "meta_0008273_EN.json")
        c = client()
        batches = [b["Dim1"] for b in c._period_batches("0008273", TOTAL, 10)]
        assert [len(b) for b in batches] == [10, 3]
        assert batches[0][0] == "S7A2011" and batches[-1][-1] == "S7A2023"
        assert period_codes(c._metadata("0008273")) == [p for b in batches for p in b]

    def test_one_request_per_batch(self, rsps):
        serve(rsps, "meta_0008273_EN.json", "data_0008273_S7A2021_S7A2022_EN.json")
        dims = {"Dim1": ["S7A2021", "S7A2022"], **TOTAL}
        chunks = list(client().get_all_data("0008273", dims, periods_per_request=2))
        assert len(chunks) == 1
        assert [row["period_code"] for row in chunks[0].data] == ["S7A2021", "S7A2022"]
        data_calls = [c for c in rsps.calls if "pindica.jsp" in c.request.url]
        assert len(data_calls) == 1
        assert "start" not in data_calls[0].request.url and "count" not in data_calls[0].request.url

    def test_chunk_size_deprecated(self, rsps):
        serve(rsps, "meta_0008273_EN.json", "data_0008273_S7A2021_S7A2022_EN.json")
        dims = {"Dim1": ["S7A2021", "S7A2022"], **TOTAL}
        with pytest.warns(DeprecationWarning, match="chunk_size is ignored"):
            list(client().get_all_data("0008273", dims, periods_per_request=2, chunk_size=5000))

    def test_periods_per_request_must_be_positive(self, rsps):
        with pytest.raises(ValueError):
            list(client().get_all_data("0008273", {"Dim1": "S7A2021"}, periods_per_request=0))


class TestAnalysisOnRealData:
    """Finding 1: the metrics and plots work on what get_data returns."""

    def test_yoy_growth(self, rsps):
        serve(rsps, "meta_0008273_EN.json", "data_0008273_S7A2021_S7A2022_EN.json")
        r = client().get_data("0008273", {"Dim1": ["S7A2021", "S7A2022"], **TOTAL})
        rows = r.calculate_yoy_growth().data
        assert rows[0]["yoy_growth"] is None
        assert rows[1]["yoy_growth"] == pytest.approx((10929704 / 10599117 - 1) * 100)

    def test_several_series_refused(self, rsps):
        serve(rsps, "meta_0008273_EN.json", "data_0008273_S7A2022_levels_EN.json")
        r = client().get_data("0008273", {"Dim1": "S7A2022", "Dim3": "T", "Dim4": "T"})
        with pytest.raises(ValueError, match="several rows per period"):
            r.calculate_yoy_growth()

    def test_yoy_compares_same_quarter(self):
        data = [
            {"period_code": "S5A20251", "value": 100.0},
            {"period_code": "S5A20254", "value": 130.0},
            {"period_code": "S5A20261", "value": 110.0},
        ]
        growth = {r["period_code"]: r["yoy_growth"] for r in calculate_yoy_growth(data)}
        assert growth["S5A20261"] == pytest.approx(10.0)  # vs S5A20251, not S5A20254
        assert growth["S5A20254"] is None

    def test_plot(self, rsps):
        pytest.importorskip("plotly")
        serve(rsps, "meta_0008273_EN.json", "data_0008273_S7A2021_S7A2022_EN.json")
        r = client().get_data("0008273", {"Dim1": ["S7A2021", "S7A2022"], **TOTAL})
        fig = r.plot()
        assert list(fig.data[0].x) == ["2021", "2022"]


class TestProvenance:
    """Every written dataset carries source, codes, periods, filter, URL, INE's extraction
    date and the licence credit; the date is INE's DataExtracao, cached or not."""

    TWO = {"Dim1": ["S7A2021", "S7A2022"], **TOTAL}
    URL = (
        "https://www.ine.pt/ine/json_indicador/pindica.jsp?op=2&varcd=0008273"
        "&Dim1=S7A2021,S7A2022&Dim2=PT&Dim3=T&Dim4=T&lang=EN"
    )

    def fetch(self, rsps, language="EN"):
        serve(
            rsps,
            f"meta_0008273_{language}.json",
            f"data_0008273_S7A2021_S7A2022_{language}.json",
        )
        return client(language).get_data("0008273", self.TWO)

    def test_fields_from_ine(self, rsps):
        from datetime import datetime, timezone

        r = self.fetch(rsps)
        assert r.extraction_date == datetime(2026, 11, 27, 16, 17, 41, 734000, timezone.utc)
        assert r.last_updated == "2026-07-17"
        assert r.metadata_url == (
            "https://www.ine.pt/bddXplorer/htdocs/minfo.jsp?var_cd=0008273&lingua=EN"
        )
        assert r.url == self.URL
        assert r.dimensions == {"Dim1": "S7A2021,S7A2022", "Dim2": "PT", "Dim3": "T", "Dim4": "T"}
        assert r.period_codes == ["S7A2021", "S7A2022"]
        assert r.periods == ["2021", "2022"]

    def test_url_is_the_request_sent(self, rsps):
        from urllib.parse import parse_qsl, urlsplit

        r = self.fetch(rsps)
        data_calls = [c for c in rsps.calls if "pindica.jsp" in c.request.url]
        sent = responses_query(data_calls[-1].request.url)
        assert dict(parse_qsl(urlsplit(r.url).query)) == sent

    def test_citation(self, rsps):
        from pyptine.provenance import ine_citation

        r = self.fetch(rsps)
        expected = (
            "Source: Statistics Portugal (INE), indicator 0008273, "
            "reference periods 2021, 2022, extracted 2026-11-27."
        )
        assert r.citation == expected
        assert ine_citation(r) == expected

    def test_citation_of_several_extracts(self, rsps):
        from pyptine.provenance import ine_citation

        serve(rsps, "meta_0008273_EN.json", "data_0008273_latest_EN.json")
        latest = client().get_data("0008273", TOTAL)
        serve(rsps, "data_0008273_S7A2021_S7A2022_EN.json")
        two = client().get_data("0008273", self.TWO)
        assert ine_citation(latest, two) == (
            "Source: Statistics Portugal (INE), indicator 0008273, "
            "reference periods 2021, 2022, 2023, extracted 2026-11-27."
        )
        with pytest.raises(ValueError, match="nothing to cite"):
            ine_citation()

    def test_missing_extraction_date_refused(self, rsps):
        from pyptine.utils.exceptions import DataProcessingError

        base, params, body = recorded("data_0008273_S7A2021_S7A2022_EN.json")
        doc = json.loads(body)
        del doc[0]["DataExtracao"]
        serve(rsps, "meta_0008273_EN.json")
        rsps.add(responses.GET, base, json=doc, match=[matchers.query_param_matcher(params)])
        with pytest.raises(DataProcessingError, match="DataExtracao"):
            client().get_data("0008273", self.TWO)

    def test_cached_response_keeps_ine_date(self, rsps, tmp_path):
        serve(rsps, "meta_0008273_EN.json", "data_0008273_S7A2021_S7A2022_EN.json")
        c = DataClient(language="EN", cache_enabled=True, cache_dir=tmp_path)
        first = c.get_data("0008273", self.TWO)
        calls = len(rsps.calls)
        again = c.get_data("0008273", self.TWO)
        assert len(rsps.calls) == calls  # served from the cache
        assert again.extraction_date == first.extraction_date
        assert again.url == first.url
        c.close()
        c.metadata_client.close()

    def test_csv_header(self, rsps, tmp_path):
        from pyptine.processors.csv import read_csv_with_metadata

        r = self.fetch(rsps)
        path = tmp_path / "out.csv"
        r.to_csv(path)
        df, meta = read_csv_with_metadata(path)
        assert list(df["value"]) == [10599117.0, 10929704.0]
        assert meta["indicator"] == "0008273"
        assert meta["language"] == "EN"
        assert meta["periods"] == "2021, 2022"
        assert meta["period_codes"] == "S7A2021, S7A2022"
        assert meta["dimensions"] == "Dim1=S7A2021,S7A2022; Dim2=PT; Dim3=T; Dim4=T"
        assert meta["url"] == self.URL
        assert meta["ine_extraction_date"] == "2026-11-27T16:17:41.734000+00:00"
        assert meta["ine_last_updated"] == "2026-07-17"
        assert meta["unit"] == "Number (No.)"
        assert meta["citation"] == r.citation
        assert "2019/1024" in meta["licence"]
        assert meta["source"].startswith("Statistics Portugal (INE)")
        assert "written" in meta

    def test_json(self, rsps, tmp_path):
        r = self.fetch(rsps, "PT")
        path = tmp_path / "out.json"
        r.to_json(path)
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["extraction_date"].startswith("2026-11-27T16:17:44.385")
        assert doc["citation"] == (
            "Source: Statistics Portugal (INE), indicator 0008273, "
            "reference periods 2021, 2022, extracted 2026-11-27."
        )
        assert doc["url"].endswith("&lang=PT")
        assert doc["dimensions"]["Dim1"] == "S7A2021,S7A2022"
        assert "2019/1024" in doc["licence"]

    def test_analysis_keeps_provenance(self, rsps):
        r = self.fetch(rsps)
        g = r.calculate_yoy_growth()
        assert (g.extraction_date, g.url, g.dimensions) == (r.extraction_date, r.url, r.dimensions)

    def test_facade_export_csv(self, rsps, tmp_path):
        from pyptine import INE
        from pyptine.processors.csv import read_csv_with_metadata

        serve(rsps, "meta_0008273_EN.json", "data_0008273_S7A2021_S7A2022_EN.json")
        path = tmp_path / "facade.csv"
        INE(cache=False).export_csv("0008273", path, dimensions=self.TWO)
        _, meta = read_csv_with_metadata(path)
        assert meta["url"] == self.URL
        assert meta["ine_extraction_date"].startswith("2026-11-27T16:17:41")

    def test_cache_ttl_applied(self, tmp_path):
        from pyptine import INE

        ine = INE(cache_dir=tmp_path, cache_ttl=600)
        assert ine.data_client.cache.data_ttl == 600
        assert ine.metadata_client.cache.metadata_ttl == 604800
        assert INE(cache_dir=tmp_path).data_client.cache.data_ttl == 86400


class TestCatalogueProvenance:
    """The catalogue's date is INE's <extraction_date>, not the local clock."""

    @pytest.mark.parametrize(
        ("language", "expected"),
        [
            ("EN", datetime(2026, 11, 27, 16, 17, 58, 67000)),
            ("PT", datetime(2026, 11, 27, 16, 17, 59, 961000)),  # same English form under PT
        ],
    )
    def test_extraction_date(self, rsps, language, expected):
        from pyptine.client.catalogue import CatalogueClient

        base, params, body = recorded(f"catalogue_opc1_0008273_{language}.xml")
        rsps.add(
            responses.GET,
            base,
            body=body,
            content_type="text/xml;charset=UTF-8",
            match=[matchers.query_param_matcher(params)],
        )
        c = CatalogueClient(language=language, cache_enabled=False)
        r = c.get_catalogue_response("0008273")
        assert r.extraction_date == expected
        assert r.extraction_date.tzinfo is None  # INE states no timezone
        assert [i.varcd for i in r] == ["0008273"]
        assert isinstance(r[0], Indicator)
        assert r.url == "https://www.ine.pt/ine/xml_indic.jsp?opc=1&varcd=0008273&lang=" + language
        assert "2019/1024" in r.licence
        assert r.model_dump(mode="json")["extraction_date"].startswith("2026-11-27T16:17:")

    @pytest.mark.parametrize(
        "text",
        [
            "Thursday, 27 November 2026, 04:17:58.067 PM",  # wrong weekday
            "Friday, 27 Novembro 2026, 04:17:58.067 PM",  # unknown month name
            "2026-11-27T16:17:58Z",
            "Friday, 27 November 2026, 13:17:58.067 PM",
            "",
        ],
    )
    def test_malformed_date_refused(self, text):
        from pyptine.provenance import parse_catalogue_date
        from pyptine.utils.exceptions import DataProcessingError

        with pytest.raises(DataProcessingError, match="extraction_date"):
            parse_catalogue_date(text)

    def test_midnight_and_noon(self):
        from pyptine.provenance import parse_catalogue_date

        assert parse_catalogue_date("Friday, 27 November 2026, 12:00:00 AM").hour == 0
        assert parse_catalogue_date("Friday, 27 November 2026, 12:30:00.5 PM") == datetime(
            2026, 11, 27, 12, 30, 0, 500000
        )


class TestCatalogueKeywords:
    """<keywords> is "INE" plus CDATA sections; a CDATA keyword may contain a comma."""

    def get(self, rsps, language):
        from pyptine.client.catalogue import CatalogueClient

        base, params, body = recorded(f"catalogue_opc1_0008273_{language}.xml")
        rsps.add(
            responses.GET,
            base,
            body=body,
            content_type="text/xml;charset=UTF-8",
            match=[matchers.query_param_matcher(params)],
        )
        return CatalogueClient(language=language, cache_enabled=False).get_indicator("0008273")

    def test_english(self, rsps):
        ind = self.get(rsps, "EN")
        assert ind.keywords == [
            "INE",
            "Resident",
            "(No.)",
            "population",
            "Population",
            "Population estimates",
        ]

    def test_portuguese_repeat_dropped(self, rsps):
        ind = self.get(rsps, "PT")
        # "População" is both a title word and the theme; kept once, in INE's order.
        assert ind.keywords == [
            "INE",
            "População",
            "(N.º)",
            "residente",
            "Estimativas de população",
        ]

    def test_comma_inside_cdata_kept(self):
        from pyptine.client.catalogue import split_keywords

        raw = (
            "INE,<![CDATA[Expenses,]]>,<![CDATA[and]]>,<![CDATA[Survey]]>,"
            "<![CDATA[Labour market]]>,<![CDATA[Work organisations]]>"
        )  # as INE sends for some indicators (2026-11-27)
        assert split_keywords(raw) == [
            "INE",
            "Expenses,",
            "and",
            "Survey",
            "Labour market",
            "Work organisations",
        ]

    def test_plain_text_split_on_commas(self):
        from pyptine.client.catalogue import split_keywords

        assert split_keywords("INE, a ,b,,a") == ["INE", "a", "b"]
        assert split_keywords("") == []

    def test_search_finds_by_keyword(self, rsps):
        from pyptine.client.catalogue import CatalogueClient
        from pyptine.search.catalog import CatalogueBrowser

        ind = self.get(rsps, "EN")
        browser = CatalogueBrowser(CatalogueClient(cache_enabled=False))
        browser._cached_indicators = [ind]
        assert [i.varcd for i in browser.search("estimates", search_fields=["keywords"])] == [
            "0008273"
        ]


class TestMetadataFromCatalogue:
    """pindicaMeta sends no description, theme, source, geographic level or URLs; the
    catalogue's opc=1 record does, and INE.get_metadata fills them from it."""

    def serve_catalogue(self, rsps, language):
        base, params, body = recorded(f"catalogue_opc1_0008273_{language}.xml")
        rsps.add(
            responses.GET,
            base,
            body=body,
            content_type="text/xml;charset=UTF-8",
            match=[matchers.query_param_matcher(params)],
        )

    def test_metadata_endpoint_alone(self, rsps):
        serve(rsps, "meta_0008273_EN.json")
        m = MetadataClient(cache_enabled=False).get_metadata("0008273")
        assert m.unit == "Number (No.)"
        assert m.periodicity == "Annual"
        for field in ("description", "theme", "subtheme", "source", "geo_last_level"):
            assert getattr(m, field) is None, field
        assert m.keywords == []
        assert not hasattr(m, "notes")

    def test_facade_fills_from_catalogue(self, rsps):
        from pyptine import INE

        serve(rsps, "meta_0008273_EN.json")
        self.serve_catalogue(rsps, "EN")
        m = INE(cache=False).get_metadata("0008273")
        assert m.theme == "Population"
        assert m.subtheme == "Population estimates"
        assert m.source == "Statistics Portugal, Annual estimates of resident population"
        assert m.geo_last_level == "Município"  # not localised under EN for this indicator
        assert m.description.startswith("Resident population (No.) by Place of residence")
        assert m.html_url == "https://www.ine.pt/xurl/indx/0008273"
        assert m.keywords[0] == "INE"
        # The metadata endpoint's own fields are kept.
        assert m.title == "Resident population (No.)" or m.title.startswith("Resident population")
        assert m.unit == "Number (No.)"
        assert m.periodicity == "Annual"
        assert m.last_period == "2023"
        assert len(m.dimensions) == 4

    def test_portuguese(self, rsps):
        from pyptine import INE

        serve(rsps, "meta_0008273_PT.json")
        self.serve_catalogue(rsps, "PT")
        m = INE(language="PT", cache=False).get_metadata("0008273")
        assert m.theme == "População"
        assert m.language == "PT"

    def test_catalogue_false_makes_one_request(self, rsps):
        from pyptine import INE

        serve(rsps, "meta_0008273_EN.json")
        m = INE(cache=False).get_metadata("0008273", catalogue=False)
        assert m.theme is None
        assert len(rsps.calls) == 1

    def test_mismatched_records_refused(self):
        from pyptine.client.metadata import with_catalogue
        from pyptine.models.indicator import IndicatorMetadata
        from pyptine.utils.exceptions import DataProcessingError

        m = IndicatorMetadata(varcd="0008273", title="t", language="EN")
        with pytest.raises(DataProcessingError, match="does not match"):
            with_catalogue(m, Indicator(varcd="0012234", title="u"))

    def test_catalogue_has_no_unit(self, rsps):
        from pyptine.client.catalogue import CatalogueClient

        self.serve_catalogue(rsps, "EN")
        assert CatalogueClient(cache_enabled=False).get_indicator("0008273").unit is None


class TestGeography:
    """Levels from codes, pinned on INE's real codes (live births 0008234 carries every level
    and the pseudo-units; 0008273 one period at every level)."""

    @staticmethod
    def births_rows():
        doc = json.loads((FIXTURES / "data_0008234_S7A2023_geo_EN.json").read_bytes())[0]
        return doc["Dados"]["2023"]

    def test_births_codes(self):
        from pyptine.geography import level_or_pseudo

        got = {r["geocod"]: level_or_pseudo(r["geocod"]) for r in self.births_rows()}
        assert got == {
            "T": "total",
            "PT": "country",
            "1": "nuts1",
            "11": "nuts2",
            "11A": "nuts3",  # NUTS III with a letter
            "11A0104": "municipality",  # Arouca
            "2004301": "municipality",  # Angra do Heroismo (Azores)
            "11A010402": "parish",
            "1120302FA": "parish",  # a Barcelos union: letters in the DICOFRE
            "YY": "abroad",
            "0": "abroad",
            "00": "abroad",
            "000": "abroad",
            "0000000": "abroad",
            "000000000": "abroad",
            "9": "unknown",
            "99": "unknown",
            "999": "unknown",
            "9999999": "unknown",
            "999999999": "unknown",
        }

    def test_names_not_used(self):
        # Under lang=EN INE labels abroad "Foreigner" and leaves the mainland "Continente":
        # the level comes from codes alone.
        names = {r["geocod"]: r["geodsg"] for r in self.births_rows()}
        assert names["0"] == "Foreigner"
        assert names["1"] == "Continente"

    def test_pseudo_unit_is_not_a_place(self):
        from pyptine.geography import geo_level
        from pyptine.utils.exceptions import DataProcessingError

        for code in ("T", "YY", "000", "9999999"):
            with pytest.raises(DataProcessingError, match="not a place"):
                geo_level(code)

    @pytest.mark.parametrize("code", ["", "12345", "12345678", "1234567890", "AB12", "0302F!"])
    def test_unknown_shape_refused(self, code):
        from pyptine.geography import level_or_pseudo
        from pyptine.utils.exceptions import DataProcessingError

        with pytest.raises(DataProcessingError, match="INE issues"):
            level_or_pseudo(code)

    def test_bare_census_codes(self):
        from pyptine.geography import geo_level, pseudo_unit

        assert geo_level("0104") == "municipality"
        assert geo_level("010402") == "parish"
        assert geo_level("0302FA") == "parish"
        assert pseudo_unit("9901") == "unknown"  # districts 99 and 00 do not exist
        assert pseudo_unit("0001") == "abroad"
        assert pseudo_unit("1119901") == "unknown"

    def test_population_levels(self, rsps):
        serve(rsps, "meta_0008273_EN.json", "data_0008273_S7A2022_levels_EN.json")
        r = client().get_data("0008273", {"Dim1": "S7A2022", "Dim3": "T", "Dim4": "T"})
        tagged = r.with_geo_level()
        from collections import Counter

        assert Counter(row["geo_level"] for row in tagged.data) == {
            "country": 1,
            "nuts1": 3,
            "nuts2": 7,
            "nuts3": 25,
            "municipality": 308,
        }
        assert "geo_level" not in r.data[0]  # the original is unchanged
        assert tagged.url == r.url  # provenance kept

        munis = r.at_level("municipality")
        assert len(munis.data) == 308
        assert sum(row["value"] for row in munis.data) == r.at_level("country").data[0]["value"]
        assert len(r.at_level("nuts1", "nuts2").data) == 10

    def test_at_level_arguments(self, rsps):
        serve(rsps, "meta_0008273_EN.json", "data_0008273_latest_EN.json")
        r = client().get_data("0008273", TOTAL)
        with pytest.raises(ValueError, match="at least one"):
            r.at_level()
        with pytest.raises(ValueError, match="Unknown level"):
            r.at_level("district")

    def test_row_without_geocod_refused(self):
        from pyptine.models.response import DataResponse
        from pyptine.utils.exceptions import DataProcessingError

        r = DataResponse(varcd="0008273", title="t", language="EN", data=[{"value": 1.0}])
        with pytest.raises(DataProcessingError, match="no geocod"):
            r.with_geo_level()


class TestExcelProvenance:
    TWO = TestProvenance.TWO

    def fetch(self, rsps):
        serve(rsps, "meta_0008273_EN.json", "data_0008273_S7A2021_S7A2022_EN.json")
        return client().get_data("0008273", self.TWO)

    @staticmethod
    def provenance_sheet(path):
        import pandas as pd

        return pd.read_excel(path, sheet_name="Provenance", dtype=str).set_index("field")

    def test_to_excel(self, rsps, tmp_path):
        import pandas as pd

        r = self.fetch(rsps)
        path = tmp_path / "out.xlsx"
        r.to_excel(path)
        assert pd.ExcelFile(path).sheet_names == ["Provenance", "Data"]
        data = pd.read_excel(path, sheet_name="Data")
        assert list(data["value"]) == [10599117.0, 10929704.0]
        assert list(data["period_code"]) == ["S7A2021", "S7A2022"]
        prov = self.provenance_sheet(path)["Data"]
        assert prov["indicator"] == "0008273"
        assert prov["citation"] == r.citation
        assert prov["url"] == TestProvenance.URL
        assert prov["dimensions"] == "Dim1=S7A2021,S7A2022; Dim2=PT; Dim3=T; Dim4=T"
        assert prov["ine_extraction_date"] == "2026-11-27T16:17:41.734000+00:00"
        assert prov["periods"] == "2021, 2022"
        assert "2019/1024" in prov["licence"]
        assert "written" in prov.index

    def test_indicator_code_kept_as_text(self, rsps, tmp_path):
        import openpyxl

        r = self.fetch(rsps)
        path = tmp_path / "out.xlsx"
        r.to_excel(path)
        ws = openpyxl.load_workbook(path)["Provenance"]
        cells = {row[0].value: row[1].value for row in ws.iter_rows(min_row=2)}
        assert cells["indicator"] == "0008273"  # leading zeros kept in the cell itself

    def test_without_provenance(self, rsps, tmp_path):
        import pandas as pd

        path = tmp_path / "bare.xlsx"
        self.fetch(rsps).to_excel(path, include_metadata=False)
        assert pd.ExcelFile(path).sheet_names == ["Data"]

    def test_several_sheets(self, rsps, tmp_path):
        import pandas as pd

        from pyptine.processors.excel import export_multiple_sheets

        serve(rsps, "meta_0008273_EN.json", "data_0008273_latest_EN.json")
        latest = client().get_data("0008273", TOTAL)
        serve(rsps, "data_0008273_S7A2021_S7A2022_EN.json")
        two = client().get_data("0008273", self.TWO)
        derived = pd.DataFrame({"x": [1]})
        path = tmp_path / "multi.xlsx"
        export_multiple_sheets(
            {"latest": latest, "two": two, "derived": derived},
            path,
            metadata={"note": "derived is mine"},
        )
        prov = self.provenance_sheet(path)
        assert list(prov.columns) == ["latest", "two", "derived"]
        assert prov.loc["periods", "latest"] == "2023"
        assert prov.loc["periods", "two"] == "2021, 2022"
        assert pd.isna(prov.loc["citation", "derived"])  # a DataFrame carries no provenance
        assert (prov.loc["note"] == "derived is mine").all()

    def test_sheet_name_rules(self, tmp_path):
        import pandas as pd

        from pyptine.processors.excel import export_multiple_sheets
        from pyptine.utils.exceptions import DataProcessingError

        df = pd.DataFrame({"x": [1]})
        with pytest.raises(DataProcessingError, match="reserved"):
            export_multiple_sheets({"Provenance": df}, tmp_path / "a.xlsx")
        with pytest.raises(DataProcessingError, match="clash"):
            export_multiple_sheets({"a" * 40: df, "a" * 35: df}, tmp_path / "b.xlsx")
        export_multiple_sheets({"a/b:c": df}, tmp_path / "c.xlsx")
        assert pd.ExcelFile(tmp_path / "c.xlsx").sheet_names == ["Provenance", "a_b_c"]

    def test_facade_export_excel(self, rsps, tmp_path):
        from pyptine import INE

        serve(rsps, "meta_0008273_EN.json", "data_0008273_S7A2021_S7A2022_EN.json")
        path = tmp_path / "facade.xlsx"
        INE(cache=False).export_excel("0008273", path, dimensions=self.TWO)
        assert self.provenance_sheet(path).loc["url", "Data"] == TestProvenance.URL


def responses_query(url: str) -> dict[str, str]:
    from urllib.parse import parse_qsl, urlsplit

    return dict(parse_qsl(urlsplit(url).query))


class TestAsync:
    """The async client parses and refuses like the sync one."""

    @staticmethod
    def transport() -> httpx.MockTransport:
        table = {}
        for name in PROVENANCE:
            if name.startswith("data_"):
                base, params, body = recorded(name)
                table[(base, frozenset(params.items()))] = body

        def handler(request: httpx.Request) -> httpx.Response:
            key = (str(request.url.copy_with(query=None)), frozenset(request.url.params.items()))
            if key not in table:
                return httpx.Response(599, text=f"no recorded response for {request.url}")
            return httpx.Response(
                200, content=table[key], headers={"content-type": "application/json"}
            )

        return httpx.MockTransport(handler)

    async def open(self) -> AsyncDataClient:
        c = AsyncDataClient(language="EN", metadata_client=MetadataClient(cache_enabled=False))
        await c.__aenter__()
        await c.client.aclose()
        c.client = httpx.AsyncClient(transport=self.transport())
        return c

    async def test_several_periods(self, rsps):
        serve(rsps, "meta_0008273_EN.json")
        c = await self.open()
        try:
            r = await c.get_data("0008273", {"Dim1": ["S7A2021", "S7A2022"], **TOTAL})
            assert [row["period_code"] for row in r.data] == ["S7A2021", "S7A2022"]
            chunks = [
                x
                async for x in c.get_all_data(
                    "0008273", {"Dim1": ["S7A2021", "S7A2022"], **TOTAL}, periods_per_request=2
                )
            ]
            assert len(chunks) == 1 and len(chunks[0].data) == 2
            assert r.url == TestProvenance.URL
            assert r.citation == chunks[0].citation
            assert r.extraction_date is not None
        finally:
            await c.__aexit__(None, None, None)

    async def test_refusal(self):
        c = await self.open()
        try:
            with pytest.raises(INERefusalError) as e:
                await c.get_data("9999999")
            assert e.value.ine_code == "1"
        finally:
            await c.__aexit__(None, None, None)


def test_no_warning_on_suppressed_markers(caplog):
    from pyptine.client.parsing import process_data_point

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        for marker in ("x", "-", "//", "...", None):
            assert process_data_point({"geocod": "PT", "valor": marker})["value"] is None
    assert "Unexpected value" not in caplog.text
