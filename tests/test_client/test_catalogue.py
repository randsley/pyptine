"""Tests for CatalogueClient, against INE's recorded responses (tests/fixtures/ine/)."""

import re
from datetime import datetime

import pytest
import responses

from pyptine.client.catalogue import CatalogueClient
from pyptine.models.indicator import Indicator
from pyptine.utils.exceptions import APIError, DataProcessingError

CATALOGUE_URL = "https://www.ine.pt/ine/xml_indic.jsp"


@pytest.fixture
def catalogue_client():
    return CatalogueClient(language="EN", cache_enabled=False)


@pytest.fixture
def catalogue_entry(fixtures_dir) -> str:
    """INE's opc=1 answer for 0008273, EN, as recorded."""
    return (fixtures_dir / "catalogue_opc1_0008273_EN.xml").read_text(encoding="utf-8")


class TestCatalogueClient:
    @responses.activate
    def test_get_indicator_success(self, catalogue_client, catalogue_entry):
        responses.add(responses.GET, CATALOGUE_URL, body=catalogue_entry, status=200)

        indicator = catalogue_client.get_indicator("0008273")

        assert isinstance(indicator, Indicator)
        assert indicator.varcd == "0008273"
        assert indicator.title == "Resident population (No.)"

    @responses.activate
    def test_get_indicator_verifies_params(self, catalogue_client, catalogue_entry):
        responses.add(responses.GET, CATALOGUE_URL, body=catalogue_entry, status=200)

        catalogue_client.get_indicator("0008273")

        url = responses.calls[0].request.url
        assert "opc=1" in url
        assert "varcd=0008273" in url

    @responses.activate
    def test_get_indicator_refuses_other_entries(self, catalogue_client, sample_catalogue):
        """An answer that is not the one entry asked for is refused, not its first entry."""
        responses.add(responses.GET, CATALOGUE_URL, body=sample_catalogue, status=200)

        with pytest.raises(DataProcessingError, match="309 entries"):
            catalogue_client.get_indicator("0012234")

    @responses.activate
    def test_get_main_indicators(self, catalogue_client, sample_catalogue):
        responses.add(responses.GET, CATALOGUE_URL, body=sample_catalogue, status=200)

        indicators = catalogue_client.get_main_indicators()

        assert len(indicators) == 309
        assert all(isinstance(ind, Indicator) for ind in indicators)
        assert "opc=3" in responses.calls[0].request.url

    def test_get_catalogue_response_refuses_missing_date(self, catalogue_client, sample_catalogue):
        """INE always sends <extraction_date>; a body without one is refused."""
        body = re.sub(r"<extraction_date>.*?</extraction_date>", "", sample_catalogue, count=1)
        with responses.RequestsMock() as rsps:
            rsps.add(responses.GET, CATALOGUE_URL, body=body)
            with pytest.raises(DataProcessingError, match="no <extraction_date>"):
                catalogue_client.get_catalogue_response()
            assert "opc=3" in rsps.calls[0].request.url

    @responses.activate
    def test_parse_indicator_fields(self, catalogue_client, catalogue_entry):
        responses.add(responses.GET, CATALOGUE_URL, body=catalogue_entry, status=200)

        indicator = catalogue_client.get_indicator("0008273")

        assert indicator.theme == "Population"
        assert indicator.subtheme == "Population estimates"
        assert indicator.periodicity == "Annual"
        assert indicator.last_period == "S7A2023"
        assert indicator.last_update == datetime(2026, 7, 17)  # INE sends DD-MM-YYYY
        # Localised for some indicators only, even under EN (see CLAUDE.md quirks).
        assert indicator.geo_last_level == "Município"
        assert indicator.html_url == "https://www.ine.pt/xurl/indx/0008273"
        assert indicator.metadata_url.endswith("pindicaMeta.jsp?varcd=0008273&lang=EN")
        assert indicator.data_url.endswith("pindica.jsp?op=2&varcd=0008273&lang=EN")
        assert indicator.source == "Statistics Portugal, Annual estimates of resident population"

    @responses.activate
    def test_invalid_xml(self, catalogue_client):
        """Test handling of invalid XML response."""
        invalid_xml = "<broken><xml"

        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/xml_indic.jsp",
            body=invalid_xml,
            status=200,
        )

        # A malformed body is retried, then reported as an API error.
        with pytest.raises(APIError, match="Invalid XML"):
            catalogue_client.get_indicator("0008273")

    @responses.activate
    def test_empty_xml_response(self, catalogue_client):
        """Test handling of empty XML response."""
        empty_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <diffgr:diffgram xmlns:msdata="urn:schemas-microsoft-com:xml-msdata"
                         xmlns:diffgr="urn:schemas-microsoft-com:xml-diffgram-v1">
            <NewDataSet xmlns="">
            </NewDataSet>
        </diffgr:diffgram>
        """

        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/xml_indic.jsp",
            body=empty_xml,
            status=200,
        )

        with pytest.raises(DataProcessingError, match="not found in catalogue"):
            catalogue_client.get_indicator("0008273")

    @responses.activate
    def test_api_error_handling(self, catalogue_client):
        """Test handling of API errors."""
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/xml_indic.jsp",
            status=500,
        )

        with pytest.raises(APIError):
            catalogue_client.get_indicator("0008273")
