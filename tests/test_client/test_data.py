"""Tests for DataClient, against INE's recorded responses (tests/fixtures/ine/)."""

import json
from unittest.mock import MagicMock

import pytest
import responses

from pyptine.client.data import DataClient
from pyptine.client.metadata import MetadataClient
from pyptine.models.response import DataResponse
from pyptine.utils.exceptions import APIError, DataProcessingError, DimensionError

DATA_URL = "https://www.ine.pt/ine/json_indicador/pindica.jsp"
TOTAL = {"Dim2": "PT", "Dim3": "T", "Dim4": "T"}  # 0008273: Portugal, both sexes, all ages


@pytest.fixture
def metadata_client_mock(sample_metadata):
    """A MetadataClient that returns INE's recorded metadata for 0008273."""
    parsed = MetadataClient(language="EN", cache_enabled=False)._parse_metadata_response(
        sample_metadata
    )
    mock = MagicMock(spec=MetadataClient)
    mock.get_metadata.return_value = parsed
    return mock


@pytest.fixture
def data_client(metadata_client_mock):
    return DataClient(language="EN", cache_enabled=False, metadata_client=metadata_client_mock)


class TestDataClient:
    @responses.activate
    def test_get_data_success(self, data_client, sample_data):
        responses.add(responses.GET, DATA_URL, json=sample_data, status=200)

        response = data_client.get_data("0008273", {"Dim1": ["S7A2021", "S7A2022"], **TOTAL})

        assert isinstance(response, DataResponse)
        assert response.varcd == "0008273"
        assert response.language == "EN"
        assert response.unit == "Number (No.)"  # from the metadata: data carry no unit
        assert [r["period_code"] for r in response.data] == ["S7A2021", "S7A2022"]
        assert [r["value"] for r in response.data] == [10599117.0, 10929704.0]

    @responses.activate
    def test_get_data_sends_codes(self, data_client, sample_data):
        responses.add(responses.GET, DATA_URL, json=sample_data, status=200)

        data_client.get_data("0008273", {"Dim1": "S7A2021,S7A2022", "Dim2": "PT"})

        assert len(responses.calls) == 1
        url = responses.calls[0].request.url
        assert "Dim1=S7A2021%2CS7A2022" in url
        assert "Dim2=PT" in url

    def test_invalid_dimension_key(self, data_client):
        with pytest.raises(DimensionError, match="Invalid dimension key 'Dim9'"):
            data_client._build_params("0008273", {"Dim9": "value"})

    def test_invalid_dimension_value(self, data_client):
        with pytest.raises(DimensionError, match="Invalid value '9999' for dimension 'Dim1'"):
            data_client._build_params("0008273", {"Dim1": "9999"})

    @responses.activate
    def test_data_to_dataframe(self, data_client, sample_data):
        responses.add(responses.GET, DATA_URL, json=sample_data, status=200)

        df = data_client.get_data(
            "0008273", {"Dim1": ["S7A2021", "S7A2022"], **TOTAL}
        ).to_dataframe()

        assert len(df) == 2
        assert {"period", "period_code", "geocod", "value"} <= set(df.columns)

    @responses.activate
    def test_get_all_data_batches_periods(self, data_client, sample_data):
        responses.add(responses.GET, DATA_URL, json=sample_data, status=200)

        chunks = list(data_client.get_all_data("0008273", TOTAL, periods_per_request=20))

        assert len(chunks) == 1  # 13 periods fit in one batch
        assert all(isinstance(chunk, DataResponse) for chunk in chunks)
        assert "Dim1=S7A2011%2C" in responses.calls[0].request.url

    def test_process_data_point(self, data_client, sample_data):
        row = sample_data[0]["Dados"]["2021"][0]  # INE's row: valor is a string
        processed = data_client._process_data_point(row)
        assert processed["value"] == 10599117.0
        assert processed["ind_string"] == row["ind_string"]

    def test_process_data_point_null_value(self, data_client):
        assert data_client._process_data_point({"geocod": "PT", "valor": None})["value"] is None

    @responses.activate
    def test_response_without_dados_refused(self, data_client, sample_data):
        body = json.loads(json.dumps(sample_data))
        del body[0]["Dados"]
        responses.add(responses.GET, DATA_URL, json=body, status=200)

        with pytest.raises(DataProcessingError, match="no Dados object"):
            data_client.get_data("0008273", {"Dim1": "S7A2021", **TOTAL})

    @responses.activate
    def test_bare_list_of_rows_refused(self, data_client, sample_data):
        """A format INE does not serve (a bare list of rows) is refused, not parsed."""
        rows = sample_data[0]["Dados"]["2021"] * 2
        responses.add(responses.GET, DATA_URL, json=rows, status=200)

        with pytest.raises(DataProcessingError, match="a list of 2 items"):
            data_client.get_data("0008273", {"Dim1": "S7A2021", **TOTAL})

    @responses.activate
    def test_api_error_handling(self, data_client):
        responses.add(responses.GET, DATA_URL, status=500)

        with pytest.raises(APIError):
            data_client.get_data("0008273")

    def test_build_params(self, data_client):
        params = data_client._build_params("0008273", {"Dim1": ["S7A2021", "S7A2022"]})
        assert params["op"] == "2"
        assert params["varcd"] == "0008273"
        assert params["Dim1"] == "S7A2021,S7A2022"
