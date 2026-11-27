"""Tests for MetadataClient, against INE's recorded responses (tests/fixtures/ine/)."""

import json

import pytest
import responses

from pyptine.client.metadata import MetadataClient
from pyptine.models.indicator import Dimension, DimensionValue, IndicatorMetadata
from pyptine.utils.exceptions import APIError, DataProcessingError

META_URL = "https://www.ine.pt/ine/json_indicador/pindicaMeta.jsp"


@pytest.fixture
def metadata_client():
    return MetadataClient(language="EN", cache_enabled=False)


class TestMetadataClient:
    @responses.activate
    def test_get_metadata_success(self, metadata_client, sample_metadata):
        responses.add(responses.GET, META_URL, json=sample_metadata, status=200)

        metadata = metadata_client.get_metadata("0008273")

        assert isinstance(metadata, IndicatorMetadata)
        assert metadata.varcd == "0008273"
        assert metadata.language == "EN"
        assert metadata.unit == "Number (No.)"
        assert metadata.periodicity == "Annual"
        assert [d.id for d in metadata.dimensions] == [1, 2, 3, 4]
        period = metadata.dimensions[0]
        assert isinstance(period, Dimension)
        assert isinstance(period.values[0], DimensionValue)

    @responses.activate
    def test_get_dimensions(self, metadata_client, sample_metadata):
        responses.add(responses.GET, META_URL, json=sample_metadata, status=200)

        dimensions = metadata_client.get_dimensions("0008273")

        assert len(dimensions) == 4
        assert all(isinstance(d, Dimension) for d in dimensions)

    @responses.activate
    def test_get_dimension_values(self, metadata_client, sample_metadata):
        responses.add(responses.GET, META_URL, json=sample_metadata, status=200)

        values = metadata_client.get_dimension_values("0008273", 1)

        codes = [v.code for v in values]
        assert codes[0] == "S7A2011" and codes[-1] == "S7A2023"
        assert len(codes) == 13
        assert values[0].label == "2011"

    @responses.activate
    def test_get_dimension_values_invalid_id(self, metadata_client, sample_metadata):
        responses.add(responses.GET, META_URL, json=sample_metadata, status=200)

        with pytest.raises(ValueError, match="Dimension 999 not found"):
            metadata_client.get_dimension_values("0008273", 999)

    @responses.activate
    def test_metadata_without_dimensoes_refused(self, metadata_client, sample_metadata):
        body = json.loads(json.dumps(sample_metadata))
        del body[0]["Dimensoes"]
        responses.add(responses.GET, META_URL, json=body, status=200)

        with pytest.raises(DataProcessingError, match="no Dimensoes"):
            metadata_client.get_metadata("0008273")

    @responses.activate
    def test_old_format_refused(self, metadata_client, sample_metadata):
        """The lower-case format INE does not serve (indicador/nome/dimensoes) is refused."""
        body = [{"indicador": "0008273", "nome": "x", "lang": "EN", "dimensoes": []}]
        responses.add(responses.GET, META_URL, json=body, status=200)

        with pytest.raises(DataProcessingError, match="no Dimensoes"):
            metadata_client.get_metadata("0008273")

    @responses.activate
    def test_api_error_handling(self, metadata_client):
        responses.add(responses.GET, META_URL, status=403)

        with pytest.raises(APIError):
            metadata_client.get_metadata("0008273")

    def test_context_manager(self):
        with MetadataClient(language="EN") as client:
            assert isinstance(client, MetadataClient)
