"""Tests for the DataFrame helpers, on INE's recorded responses (tests/fixtures/ine/)."""

import json
from pathlib import Path

import pandas as pd
import pytest

from pyptine.client.metadata import MetadataClient
from pyptine.client.parsing import parse_data_response
from pyptine.models.response import DataResponse
from pyptine.processors.dataframe import (
    aggregate_by_period,
    clean_dataframe,
    filter_by_geography,
    get_latest_period,
    json_to_dataframe,
    merge_metadata,
    pivot_by_dimension,
)
from pyptine.utils.exceptions import DataProcessingError

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ine"


def load(name: str):
    return json.loads((FIXTURES / name).read_bytes())


def metadata(varcd: str):
    return MetadataClient(cache_enabled=False)._parse_metadata_response(
        load(f"meta_{varcd}_EN.json")
    )


def recorded(varcd: str, data: str) -> DataResponse:
    md = metadata(varcd)
    return parse_data_response(varcd, load(data), "EN", lambda: md)


@pytest.fixture
def years() -> DataResponse:
    """0008273, Portugal, both sexes, all ages, 2021 and 2022."""
    return recorded("0008273", "data_0008273_S7A2021_S7A2022_EN.json")


@pytest.fixture
def districts() -> DataResponse:
    """0004167, 2025, both sexes, all ages: Portugal, 3 NUTS I regions, 18 districts."""
    return recorded("0004167", "data_0004167_S7A2025_EN.json")


class TestJsonToDataFrame:
    def test_rows(self, years):
        df = json_to_dataframe(years.data)

        assert len(df) == 2
        # Names stay as INE and the parser give them (no underscores replaced).
        assert {"period", "period_code", "dim_3", "dim_3_t", "value"} <= set(df.columns)
        assert pd.api.types.is_numeric_dtype(df["value"])
        assert list(df["period_code"]) == ["S7A2021", "S7A2022"]  # not parsed as dates

    def test_raw_rows_value_numeric(self):
        rows = load("data_0008273_S7A2021_S7A2022_EN.json")[0]["Dados"]["2021"]
        df = json_to_dataframe(rows)
        assert df["valor"].iloc[0] == 10599117  # INE's valor is a string

    def test_quarter_labels_kept(self):
        """INE's labels ("1st Quarter 2026") are not dates: kept, never turned into NaT."""
        labels = [v.label for v in metadata("0012234").dimensions[0].values][:3]
        df = json_to_dataframe([{"period": label, "value": 1.0} for label in labels])
        assert list(df["period"]) == labels

    def test_response_object_refused(self):
        with pytest.raises(DataProcessingError, match="list of rows"):
            json_to_dataframe(load("data_0008273_S7A2021_S7A2022_EN.json")[0])

    def test_empty(self):
        assert json_to_dataframe([]).empty


class TestPivotByDimension:
    def test_pivot(self, districts):
        df = districts.to_dataframe()[["period_code", "geocod", "value"]]

        pivoted = pivot_by_dimension(df, "geocod")

        assert pivoted.loc["S7A2025", ("value", "11")] == 2655832  # Lisboa district
        assert pivoted.loc["S7A2025", ("value", "PT")] == 11424031

    def test_list_value_column(self, districts):
        df = districts.to_dataframe()[["period_code", "geocod", "value"]]
        df["double"] = df["value"] * 2

        pivoted = pivot_by_dimension(df, "geocod", value_column=["value", "double"])

        assert pivoted.loc["S7A2025", ("double", "PT")] == 2 * 11424031


class TestAggregateByPeriod:
    def test_sum_over_one_level(self, districts):
        """Sum one level only: the 18 districts add up to the mainland (NUTS I "1")."""
        df = districts.at_level("district").to_dataframe()

        agg = aggregate_by_period(df)

        assert list(agg.columns) == ["period_code", "value"]
        assert agg["value"].iloc[0] == 10912573

    def test_list_value_column(self, years):
        df = years.to_dataframe()
        df["double"] = df["value"] * 2

        agg = aggregate_by_period(df, value_column=["value", "double"])

        assert list(agg["double"]) == [2 * 10599117, 2 * 10929704]

    def test_missing_column(self):
        with pytest.raises(DataProcessingError, match="Period column"):
            aggregate_by_period(pd.DataFrame({"data": [1, 2, 3]}))


class TestGetLatestPeriod:
    def test_latest(self, years):
        latest = get_latest_period(years.to_dataframe())
        assert list(latest["period_code"]) == ["S7A2022"]

    def test_codes_sort_where_labels_do_not(self):
        """Quarterly labels sort wrongly ("4th Quarter 2019" after "1st Quarter 2026");
        the default column, period_code, sorts chronologically."""
        values = metadata("0012234").dimensions[0].values
        df = pd.DataFrame(
            {"period": [v.label for v in values], "period_code": [v.code for v in values]}
        )
        assert get_latest_period(df)["period_code"].iloc[0] == values[-1].code
        assert get_latest_period(df, period_column="period")["period"].iloc[0] != values[-1].label

    def test_n(self, years):
        latest = get_latest_period(years.to_dataframe(), n=2)
        assert set(latest["period_code"]) == {"S7A2021", "S7A2022"}

    def test_missing_column(self):
        with pytest.raises(ValueError, match="Period column"):
            get_latest_period(pd.DataFrame({"data": [1, 2, 3]}))


class TestFilterByGeography:
    def test_by_name(self, districts):
        filtered = filter_by_geography(districts.to_dataframe(), "Lisboa")
        assert list(filtered["geocod"]) == ["11"]

    def test_case_insensitive(self, districts):
        assert len(filter_by_geography(districts.to_dataframe(), "LISBOA")) == 1


class TestCleanDataFrame:
    def test_drop_internal_columns(self, years):
        df = years.to_dataframe()
        df["_internal"] = 1

        cleaned = clean_dataframe(df)

        assert "_internal" not in cleaned.columns
        assert "period_code" in cleaned.columns

    def test_rename_columns(self, years):
        renamed = clean_dataframe(
            years.to_dataframe(), drop_internal_columns=False, rename_columns={"geodsg": "place"}
        )
        assert "place" in renamed.columns and "geodsg" not in renamed.columns


class TestMergeMetadata:
    def test_add_metadata_columns(self, years):
        result = merge_metadata(
            years.to_dataframe(), {"indicator": years.varcd, "unit": years.unit}
        )
        assert all(result["meta_indicator"] == "0008273")
        assert all(result["meta_unit"] == "Number (No.)")

    def test_custom_prefix(self, years):
        result = merge_metadata(years.to_dataframe(), {"source": "INE"}, prefix="info_")
        assert "info_source" in result.columns
