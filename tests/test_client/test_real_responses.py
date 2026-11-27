"""Tests pinned against real INE responses (tests/fixtures/ine/, see PROVENANCE.json).

Each fixture is served for exactly the query it was recorded with, so these tests also pin the
URLs pyptine builds. Recorded 2026-11-27 by tools/capture_fixtures.py.
"""

import json
import warnings
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
