"""End-to-end tests of INE, on INE's recorded responses (tests/fixtures/ine/).

sample_metadata and sample_data are 0008273 (resident population); sample_catalogue is the
main group (opc=3), whose titles are Portuguese under either language."""

import json

import pandas as pd
import pytest
import responses

from pyptine import INE


class TestINEIntegration:
    """Integration tests for INE class."""

    @responses.activate
    def test_basic_workflow(self, sample_catalogue, sample_metadata, sample_data, tmp_path):
        """Test basic workflow: search, get data, export."""
        # Mock catalogue endpoint
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/xml_indic.jsp",
            body=sample_catalogue,
            status=200,
            content_type="application/xml",
        )

        # Mock metadata endpoint
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/json_indicador/pindicaMeta.jsp",
            json=sample_metadata,
            status=200,
        )

        # Mock data endpoint
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/json_indicador/pindica.jsp",
            json=sample_data,
            status=200,
        )

        # Initialize client
        ine = INE(language="EN", cache=False)

        # Search for indicators
        results = ine.search("population")
        assert len(results) == 22
        assert all(
            "population" in " ".join([ind.title, ind.description or "", *ind.keywords]).lower()
            for ind in results
        )

        # Get data as DataFrame
        response = ine.get_data(
            "0008273", {"Dim1": ["S7A2021", "S7A2022"], "Dim2": "PT", "Dim3": "T", "Dim4": "T"}
        )
        df = response.to_dataframe()
        assert list(df["value"]) == [10599117.0, 10929704.0]

        # Get data as JSON
        json_output_file = tmp_path / "basic_workflow_output.json"
        response.to_json(json_output_file)
        assert json_output_file.exists()
        assert "0008273" in json_output_file.read_text()

        # Get data as dict
        data_dict = response.to_dict()
        assert isinstance(data_dict, dict)

    @responses.activate
    def test_metadata_and_dimensions(self, sample_metadata):
        """Test metadata and dimension retrieval."""
        # Mock metadata endpoint
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/json_indicador/pindicaMeta.jsp",
            json=sample_metadata,
            status=200,
        )

        ine = INE(language="EN", cache=False)

        # Get metadata
        metadata = ine.get_metadata("0008273", catalogue=False)
        assert metadata.varcd == "0008273"
        assert metadata.title
        assert len(metadata.dimensions) > 0

        # Get dimensions
        dimensions = ine.get_dimensions("0008273")
        assert len(dimensions) > 0
        assert all(hasattr(dim, "name") for dim in dimensions)
        assert all(hasattr(dim, "values") for dim in dimensions)

    @responses.activate
    def test_export_csv(self, sample_metadata, sample_data, tmp_path):
        """Test CSV export functionality."""
        # Mock endpoints
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/json_indicador/pindicaMeta.jsp",
            json=sample_metadata,
            status=200,
        )

        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/json_indicador/pindica.jsp",
            json=sample_data,
            status=200,
        )

        ine = INE(language="EN", cache=False)

        # Export to CSV
        output_file = tmp_path / "test_export.csv"
        ine.export_csv("0008273", output_file)

        # Verify file exists and has content
        assert output_file.exists()
        assert output_file.stat().st_size > 0

        # Read back and verify
        df = pd.read_csv(output_file, comment="#")
        assert not df.empty

    @responses.activate
    def test_export_json(self, sample_metadata, sample_data, tmp_path):
        """Test JSON export functionality."""
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/json_indicador/pindicaMeta.jsp",
            json=sample_metadata,
            status=200,
        )
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/json_indicador/pindica.jsp",
            json=sample_data,
            status=200,
        )

        ine = INE(language="EN", cache=False)

        # Export to JSON
        output_file = tmp_path / "test_export.json"
        ine.export_json("0008273", output_file)
        assert output_file.exists()

        # Verify JSON content
        with open(output_file) as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert data["varcd"] == "0008273"
        assert len(data["data"]) == 2
        assert output_file.stat().st_size > 0

    @responses.activate
    def test_theme_filtering(self, sample_catalogue):
        """Test theme-based filtering."""
        # Mock catalogue endpoint
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/xml_indic.jsp",
            body=sample_catalogue,
            status=200,
            content_type="application/xml",
        )

        ine = INE(language="EN", cache=False)

        # Get all themes
        themes = ine.list_themes()
        assert len(themes) == 24

        indicators = ine.search(query="", theme="Population")
        assert len(indicators) == 21
        assert all(ind.theme == "Population" for ind in indicators)

    @responses.activate
    def test_indicator_validation(self, fixtures_dir):
        """Test indicator validation."""
        # Mock catalogue endpoint
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/xml_indic.jsp",
            body=(fixtures_dir / "catalogue_opc1_0008273_EN.xml").read_bytes(),
            status=200,
            content_type="application/xml",
        )

        ine = INE(language="EN", cache=False)

        # Valid indicator
        assert ine.validate_indicator("0008273")

        # Mock invalid indicator
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/xml_indic.jsp",
            json={"error": "Invalid indicator"},
            status=404,
        )

        # Invalid indicator
        assert not ine.validate_indicator("invalid_code")

    def test_one_cache_for_all_clients(self, tmp_path):
        """INE opens one DiskCache (two SQLite sessions), not one per client."""
        ine = INE(language="EN", cache=True, cache_dir=tmp_path, cache_ttl=60)
        clients = (
            ine.base_client,
            ine.catalogue_client,
            ine.metadata_client,
            ine.data_client,
            ine.data_client.metadata_client,
        )
        assert len({id(c.cache) for c in clients}) == 1
        assert ine.data_client.cache.data_ttl == 60
        assert ine.data_client.cache.get_cache_dir() == tmp_path
        assert all(c.cache is None for c in (INE(cache=False).base_client,))

    def test_cache_management(self, tmp_path):
        """Test cache management."""
        # Initialize with cache
        ine = INE(language="EN", cache=True, cache_dir=tmp_path)

        # Get cache info
        info = ine.get_cache_info()
        assert info["enabled"] is True
        assert "metadata_cache" in info
        assert "data_cache" in info

        # Clear cache
        ine.clear_cache()

        # Test without cache
        ine_no_cache = INE(language="EN", cache=False)
        info = ine_no_cache.get_cache_info()
        assert info["enabled"] is False

    @responses.activate
    def test_dimension_filtering(self, sample_metadata, sample_data):
        """Test data filtering by dimensions."""
        # Mock endpoints
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/json_indicador/pindicaMeta.jsp",
            json=sample_metadata,
            status=200,
        )

        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/json_indicador/pindica.jsp",
            json=sample_data,
            status=200,
        )

        ine = INE(language="EN", cache=False)

        # Get dimensions
        dimensions = ine.get_dimensions("0008273")
        assert len(dimensions) > 0

        # Get data with dimension filter
        if dimensions and dimensions[0].values:
            dim_filter = {"Dim1": dimensions[0].values[0].code}
            response = ine.get_data("0008273", dimensions=dim_filter)
            df = response.to_dataframe()
            assert isinstance(df, pd.DataFrame)

    def test_language_setting(self):
        """Test language configuration."""
        # English
        ine_en = INE(language="EN")
        assert ine_en.language == "EN"

        # Portuguese
        ine_pt = INE(language="PT")
        assert ine_pt.language == "PT"

        # Case insensitive
        ine_lower = INE(language="en")
        assert ine_lower.language == "EN"

        # Invalid language
        with pytest.raises(ValueError, match="Language must be"):
            INE(language="FR")

    @responses.activate
    def test_error_handling(self):
        """Test error handling for API failures."""
        # Mock API error
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/json_indicador/pindica.jsp",
            json={"error": "Internal server error"},
            status=500,
        )

        ine = INE(language="EN", cache=False)

        from pyptine.utils.exceptions import APIError

        # Should raise APIError after retries
        with pytest.raises(APIError):  # Will be APIError or similar
            ine.get_data("0008273")
