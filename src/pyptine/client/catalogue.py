"""Catalogue client for INE Portugal API."""

import html
import logging
import re
from datetime import datetime
from typing import Any, Optional, cast
from xml.etree import ElementTree as ET

from pyptine.client.base import INEClient
from pyptine.models.indicator import Indicator
from pyptine.models.response import CatalogueResponse
from pyptine.provenance import parse_catalogue_date, request_url
from pyptine.utils.exceptions import DataProcessingError

logger = logging.getLogger(__name__)

# <keywords> is a bare "INE" then CDATA sections: three words cut from the title, punctuation
# included ("Expenses,", "2021)"), then the theme and the subtheme. A CDATA keyword may itself
# contain a comma (2,642 of 13,290 indicators on 2026-11-27, either language), and ElementTree
# merges the sections into one text, so keywords are split on the raw CDATA boundaries.
_INDICATOR = re.compile(r'<indicator\b[^>]*?\bid="([^"]*)"[^>]*>(.*?)</indicator>', re.S)
_KEYWORDS = re.compile(r"<keywords>(.*?)</keywords>", re.S)
_KEYWORD_PART = re.compile(r"<!\[CDATA\[(.*?)\]\]>|([^,<]+)", re.S)


def split_keywords(raw: str) -> list[str]:
    """INE's keywords from the inside of one ``<keywords>`` element.

    Each CDATA section is one keyword, commas and all; plain text between them is split on
    commas. Whitespace is trimmed; empty and repeated keywords are dropped (theme and
    subtheme are often equal); INE's order is kept.
    """
    out: list[str] = []
    for m in _KEYWORD_PART.finditer(raw):
        cdata, plain = m.group(1), m.group(2)
        parts = [cdata] if cdata is not None else (plain or "").split(",")
        for part in parts:
            word = html.unescape(part).strip() if cdata is None else part.strip()
            if word and word not in out:
                out.append(word)
    return out


def raw_keywords(xml: str) -> list[tuple[str, list[str]]]:
    """``(indicator id, keywords)`` for each ``<indicator>`` in a catalogue, in order."""
    result = []
    for m in _INDICATOR.finditer(xml):
        kw = _KEYWORDS.search(m.group(2))
        result.append((m.group(1), split_keywords(kw.group(1)) if kw else []))
    return result


class CatalogueClient(INEClient):
    """Client for INE catalogue API endpoint.

    Fetches and parses indicator catalogue from XML responses.

    Example:
        >>> client = CatalogueClient(language="EN")
        >>> indicator = client.get_indicator("0004167")
        >>> print(indicator.title)
        'Resident population'
    """

    CATALOGUE_ENDPOINT = "/ine/xml_indic.jsp"

    def _get_element_text(self, element: ET.Element, tag: str, default: str = "") -> str:
        """Helper to get text from a child element."""
        child = element.find(tag)
        if child is not None and child.text:
            return child.text.strip()
        return default

    def get_indicator(self, varcd: str) -> Indicator:
        """Get single indicator metadata from catalogue.

        Args:
            varcd: Indicator code (e.g., "0004167")

        Returns:
            Indicator object with metadata

        Example:
            >>> client = CatalogueClient()
            >>> indicator = client.get_indicator("0004167")
            >>> print(f"{indicator.varcd}: {indicator.title}")
        """
        logger.info(f"Fetching indicator {varcd} from catalogue")

        params = {
            "opc": "1",  # Single indicator
            "varcd": varcd,
        }

        try:
            xml_response = self._make_request(
                self.CATALOGUE_ENDPOINT, params=params, response_format="xml"
            )

            indicators = self._parse_catalogue_xml(cast(str, xml_response))

            if not indicators:
                raise DataProcessingError(f"Indicator {varcd} not found in catalogue")

            logger.info(f"Retrieved indicator {varcd}: {indicators[0].title}")
            return indicators[0]

        except Exception as e:
            logger.error(f"Failed to get indicator {varcd}: {str(e)}")
            raise

    def get_main_indicators(self) -> list[Indicator]:
        """Get all main indicators from catalogue.

        Returns:
            List of Indicator objects

        Note:
            INE's main group (``opc=3``, 309 indicators on 2026-11-27) localises only
            ``theme`` and ``subtheme``: its titles and descriptions are **Portuguese under
            either language**. It is also not all of INE (indicators leave it while still
            served). For English titles, or every indicator, use
            :meth:`get_complete_catalogue` (``opc=2``) or :meth:`get_indicator` (``opc=1``),
            which honour the language.

        Example:
            >>> client = CatalogueClient()
            >>> indicators = client.get_main_indicators()
            >>> print(f"Found {len(indicators)} indicators")
        """
        logger.info("Fetching main indicators group from catalogue")

        params = {
            "opc": "3",  # Main indicators group
        }

        try:
            xml_response = self._make_request(
                self.CATALOGUE_ENDPOINT, params=params, response_format="xml"
            )

            indicators = self._parse_catalogue_xml(cast(str, xml_response))

            logger.info(f"Retrieved {len(indicators)} main indicators")
            return indicators

        except Exception as e:
            logger.error(f"Failed to get main indicators: {str(e)}")
            raise

    def get_complete_catalogue(self, progress_callback: Optional[Any] = None) -> list[Indicator]:
        """Get the complete catalogue of indicators.

        Args:
            progress_callback: Optional callback function(downloaded_bytes, total_bytes)

        Returns:
            List of all Indicator objects in the complete catalogue.

        Raises:
            DataProcessingError: If XML parsing fails.
            Exception: For other API request failures.

        Example:
            >>> client = CatalogueClient()
            >>> all_indicators = client.get_complete_catalogue()
            >>> print(f"Found {len(all_indicators)} indicators in complete catalogue")
        """
        logger.info("Fetching complete catalogue of indicators (opc=2)")

        params = {
            "opc": "2",  # Complete catalogue
        }

        try:
            xml_response = self._make_request(
                self.CATALOGUE_ENDPOINT,
                params=params,
                response_format="xml",
                progress_callback=progress_callback,
            )

            indicators = self._parse_catalogue_xml(cast(str, xml_response))

            logger.info(f"Retrieved {len(indicators)} indicators from complete catalogue")
            return indicators

        except Exception as e:
            logger.error(f"Failed to get complete catalogue: {str(e)}")
            raise

    def get_catalogue_response(
        self,
        varcd: Optional[str] = None,
        complete: bool = False,
        progress_callback: Optional[Any] = None,
    ) -> CatalogueResponse:
        """Catalogue indicators with INE's extraction date and the request URL.

        Args:
            varcd: One indicator (``opc=1``)
            complete: Without ``varcd``: the whole catalogue (``opc=2``, ~13,000 indicators,
                ~20 s) instead of the main group (``opc=3``, whose titles are Portuguese
                under either language)
            progress_callback: Optional callback function(downloaded_bytes, total_bytes)

        Returns:
            CatalogueResponse whose ``extraction_date`` is INE's ``<extraction_date>``

        Raises:
            DataProcessingError: If INE's ``<extraction_date>`` is missing or malformed, or
                ``varcd`` is not in the catalogue

        Example:
            >>> client = CatalogueClient()
            >>> response = client.get_catalogue_response("0008273")
            >>> response.extraction_date
        """
        opc = "2" if complete else "3"
        params = {"opc": "1", "varcd": varcd} if varcd else {"opc": opc}
        xml = cast(
            str,
            self._make_request(
                self.CATALOGUE_ENDPOINT,
                params=params,
                response_format="xml",
                progress_callback=progress_callback,
            ),
        )
        indicators = self._parse_catalogue_xml(xml)
        if varcd and not indicators:
            raise DataProcessingError(f"Indicator {varcd} not found in catalogue")
        try:
            date_text = ET.fromstring(xml).findtext("extraction_date")
        except ET.ParseError as e:  # already parsed above; kept for the type checker
            raise DataProcessingError(f"Invalid XML response: {e}") from e
        if date_text is None:
            raise DataProcessingError(
                "INE's catalogue response has no <extraction_date>, which provenance needs"
            )
        return CatalogueResponse(
            indicators=indicators,
            language=self.language,
            total_count=len(indicators),
            extraction_date=parse_catalogue_date(date_text),
            url=request_url(
                self.BASE_URL, self.CATALOGUE_ENDPOINT, {**params, "lang": self.language}
            ),
        )

    def _parse_catalogue_xml(self, xml_string: str) -> list[Indicator]:
        """Parse XML catalogue response into list of Indicators.

        Args:
            xml_string: Raw XML response from API

        Returns:
            List of parsed Indicator objects

        Raises:
            DataProcessingError: If XML parsing fails
        """
        try:
            root = ET.fromstring(xml_string)

            indicators: list[Indicator] = []

            # Find all <indicator> elements directly under the <catalog> root
            indicator_elements = root.findall(".//indicator")

            if not indicator_elements:
                logger.warning("Could not find any <indicator> elements in XML")
                return indicators

            logger.debug(f"Found {len(indicator_elements)} indicator(s) in XML")

            keywords = raw_keywords(xml_string)
            if len(keywords) != len(indicator_elements):
                raise DataProcessingError(
                    f"Catalogue has {len(indicator_elements)} <indicator> elements but "
                    f"{len(keywords)} could be matched to their raw <keywords>"
                )

            for indicator_elem, (kw_varcd, kws) in zip(indicator_elements, keywords):
                indicator = self._parse_indicator_xml(indicator_elem)
                if indicator:
                    if kw_varcd != indicator.varcd:
                        raise DataProcessingError(
                            f"Catalogue keywords out of step: <indicator id={kw_varcd!r}> "
                            f"parsed as {indicator.varcd!r}"
                        )
                    indicators.append(indicator.model_copy(update={"keywords": kws}))

            return indicators

        except DataProcessingError:
            raise
        except ET.ParseError as e:
            logger.error(f"XML parsing error: {str(e)}")
            raise DataProcessingError(f"Invalid XML response: {str(e)}") from e
        except Exception as e:
            logger.error(f"Failed to parse XML response: {str(e)}")
            raise DataProcessingError(f"Failed to parse catalogue: {str(e)}") from e

    def _parse_indicator_xml(self, indicator_elem: ET.Element) -> Optional[Indicator]:
        """Parse a single <indicator> element into an Indicator.

        Args:
            indicator_elem: XML element representing one indicator

        Returns:
            Parsed Indicator object or None if parsing fails
        """
        try:
            # Extract fields using new tag names
            varcd = self._get_element_text(indicator_elem, "varcd")
            if not varcd:
                logger.warning("Found indicator without varcd, skipping")
                return None

            title = self._get_element_text(indicator_elem, "title")
            theme = self._get_element_text(indicator_elem, "theme")
            subtheme = self._get_element_text(indicator_elem, "subtheme")
            periodicity = self._get_element_text(indicator_elem, "periodicity")
            geo_last_level = self._get_element_text(indicator_elem, "geo_lastlevel")
            source = self._get_element_text(indicator_elem, "source")

            # URLs are nested under <html> and <json>
            html_elem = indicator_elem.find("html")
            html_url = self._get_element_text(html_elem, "bdd_url") if html_elem is not None else ""

            json_elem = indicator_elem.find("json")
            metadata_url = (
                self._get_element_text(json_elem, "json_metainfo") if json_elem is not None else ""
            )
            data_url = (
                self._get_element_text(json_elem, "json_dataset") if json_elem is not None else ""
            )

            # Parse last_period and last_update from <dates>
            last_period = None
            last_update = None
            dates_elem = indicator_elem.find("dates")
            if dates_elem is not None:
                last_period = self._get_element_text(dates_elem, "last_period_available")
                last_update_str = self._get_element_text(dates_elem, "last_update")
                if last_update_str:
                    try:
                        # The date format is 'DD-MM-YYYY'
                        last_update = datetime.strptime(last_update_str, "%d-%m-%Y")
                    except ValueError:
                        logger.debug(f"Could not parse date: {last_update_str}")

            description = self._get_element_text(indicator_elem, "description")

            # Create Indicator object
            indicator = Indicator(
                varcd=varcd,
                title=title,
                description=description or None,
                theme=theme or None,
                subtheme=subtheme or None,
                periodicity=periodicity or None,
                last_period=last_period or None,
                last_update=last_update,
                geo_last_level=geo_last_level or None,
                html_url=html_url or None,
                metadata_url=metadata_url or None,
                data_url=data_url or None,
                source=source or None,
            )

            return indicator

        except Exception as e:
            logger.warning(f"Failed to parse indicator XML: {str(e)}")
            return None
