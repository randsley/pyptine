"""Tests for CatalogueBrowser functionality."""

import pytest
import responses

from pyptine.client.catalogue import CatalogueClient
from pyptine.models.indicator import Indicator
from pyptine.search.catalog import CatalogueBrowser


@pytest.fixture
def catalogue_entry(fixtures_dir) -> str:
    """INE's opc=1 answer for 0008273, EN, as recorded."""
    return (fixtures_dir / "catalogue_opc1_0008273_EN.xml").read_text(encoding="utf-8")


class TestCatalogueBrowser:
    """Tests for CatalogueBrowser class."""

    @pytest.fixture
    def browser(self):
        """Create browser instance."""
        client = CatalogueClient(language="EN", cache_enabled=False)
        return CatalogueBrowser(client, language="EN")

    @responses.activate
    def test_get_all_indicators(self, browser, sample_catalogue):
        """Test fetching all indicators."""
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/xml_indic.jsp",
            body=sample_catalogue,
            status=200,
            content_type="application/xml",
        )

        indicators = browser.get_all_indicators()

        assert len(indicators) > 0
        assert all(isinstance(ind, Indicator) for ind in indicators)

    @responses.activate
    def test_search_basic(self, browser, sample_catalogue):
        """Test basic search functionality."""
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/xml_indic.jsp",
            body=sample_catalogue,
            status=200,
            content_type="application/xml",
        )

        results = browser.search("population")

        # 0008273's keywords carry its theme "Population" (opc=3, recorded 2026-11-27).
        assert len(results) == 22
        assert all(isinstance(ind, Indicator) for ind in results)

        # Check that results match search query
        for ind in results:
            text_fields = [
                ind.title.lower(),
                (ind.description or "").lower(),
                " ".join(ind.keywords).lower(),
            ]
            assert any("population" in field for field in text_fields)

    @responses.activate
    def test_search_case_sensitive(self, browser, sample_catalogue):
        """Test case-sensitive search."""
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/xml_indic.jsp",
            body=sample_catalogue,
            status=200,
            content_type="application/xml",
        )

        # Case-insensitive (default)
        assert len(browser.search("POPULATION")) == 22
        assert len(browser.search("Population", case_sensitive=True)) == 22
        assert len(browser.search("population", case_sensitive=True)) == 3

    @responses.activate
    def test_search_specific_fields(self, browser, sample_catalogue):
        """Test searching specific fields."""
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/xml_indic.jsp",
            body=sample_catalogue,
            status=200,
            content_type="application/xml",
        )

        # Search only in title
        # opc=3 titles are Portuguese under either language (a quirk of INE's main group),
        # so an English word matches no title.
        assert browser.search("population", search_fields=["title"]) == []
        assert len(browser.search("população", search_fields=["title"])) > 0

    @responses.activate
    def test_search_empty_query(self, browser, sample_catalogue):
        """Test search with empty query."""
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/xml_indic.jsp",
            body=sample_catalogue,
            status=200,
            content_type="application/xml",
        )
        results = browser.search("")
        assert len(results) > 0
        assert all(isinstance(ind, Indicator) for ind in results)

    @responses.activate
    def test_list_themes(self, browser, sample_catalogue):
        """Test listing all themes."""
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/xml_indic.jsp",
            body=sample_catalogue,
            status=200,
            content_type="application/xml",
        )

        themes = browser.list_themes()

        assert isinstance(themes, list)
        assert all(isinstance(theme, str) for theme in themes)
        assert themes == sorted(themes)
        assert len(themes) == 24

    @responses.activate
    def test_list_subthemes(self, browser, sample_catalogue):
        """Test listing subthemes."""
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/xml_indic.jsp",
            body=sample_catalogue,
            status=200,
            content_type="application/xml",
        )

        # Get all subthemes
        subthemes = browser.list_subthemes()
        assert isinstance(subthemes, list)

        # Get subthemes for specific theme
        themes = browser.list_themes()
        if themes:
            theme_subthemes = browser.search(query="", theme=themes[0])
            assert isinstance(theme_subthemes, list)

    @responses.activate
    def test_get_recently_updated(self, browser, sample_catalogue):
        """Test getting recently updated indicators."""
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/xml_indic.jsp",
            body=sample_catalogue,
            status=200,
            content_type="application/xml",
        )

        recent = browser.get_recently_updated(limit=5)

        assert len(recent) <= 5
        assert all(isinstance(ind, Indicator) for ind in recent)

        # Check that indicators with dates are included
        if len(recent) > 1:
            # Should be sorted by date descending
            for i in range(len(recent) - 1):
                if recent[i].last_update and recent[i + 1].last_update:
                    assert recent[i].last_update >= recent[i + 1].last_update

    @responses.activate
    def test_get_by_code(self, browser, catalogue_entry):
        """Test getting indicator by code."""
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/xml_indic.jsp",
            body=catalogue_entry,
            status=200,
            content_type="application/xml",
        )

        indicator = browser.get_by_code("0008273")

        assert isinstance(indicator, Indicator)
        assert indicator.varcd == "0008273"

    @responses.activate
    def test_get_by_code_invalid(self, browser):
        """Test getting invalid indicator code."""
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/xml_indic.jsp",
            json={"error": "Not found"},
            status=404,
        )

        indicator = browser.get_by_code("invalid")
        assert indicator is None

    @responses.activate
    def test_validate_indicator(self, browser, catalogue_entry):
        """Test indicator validation."""
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/xml_indic.jsp",
            body=catalogue_entry,
            status=200,
            content_type="application/xml",
        )

        # Valid indicator
        assert browser.validate_indicator("0008273") is True

        # Invalid indicator (will fail when trying to get it)
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/xml_indic.jsp",
            json={"error": "Not found"},
            status=404,
        )

        assert browser.validate_indicator("invalid") is False

    @responses.activate
    def test_cache_functionality(self, browser, sample_catalogue):
        """Test cache clearing."""
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/xml_indic.jsp",
            body=sample_catalogue,
            status=200,
            content_type="application/xml",
        )

        # First call - fetches from API
        indicators1 = browser.get_all_indicators()
        assert len(indicators1) > 0

        # Second call - uses cache
        indicators2 = browser.get_all_indicators(use_cache=True)
        assert len(indicators2) == len(indicators1)

        # Clear cache
        browser.clear_cache()

        # Third call - fetches from API again
        indicators3 = browser.get_all_indicators(use_cache=False)
        assert len(indicators3) == len(indicators1)

    @responses.activate
    def test_exact_match_search(self, browser, sample_catalogue):
        """Test exact match search."""
        responses.add(
            responses.GET,
            "https://www.ine.pt/ine/xml_indic.jsp",
            body=sample_catalogue,
            status=200,
            content_type="application/xml",
        )

        # Substring match (default)
        results_substring = browser.search("pop", exact_match=False)

        # Exact match
        results_exact = browser.search("pop", exact_match=True)

        # Exact match should return fewer or equal results
        assert len(results_exact) <= len(results_substring)
