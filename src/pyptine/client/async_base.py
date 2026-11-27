"""Async HTTP client for INE Portugal API."""

import asyncio
import logging
import time
from collections.abc import Sequence
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from pyptine.__version__ import __version__
from pyptine.client import retry
from pyptine.client.parsing import check_catalogue_body, check_success
from pyptine.utils.exceptions import APIError, RateLimitError

logger = logging.getLogger(__name__)


class AsyncINEClient:
    """Async HTTP client for INE Portugal API.

    Same retry and timeout policy as the sync client (:mod:`pyptine.client.retry`): HTTP 404,
    429 and 5xx, failed connections and malformed bodies are retried after 5, 20 and 60 s; a
    read timeout and INE refusals are not. No cache.

    Args:
        language: Language for API responses ("EN" or "PT")
        timeout: Read timeout in seconds (default 130; INE's gateway answers 504 after 120 s)
        max_retries: Retries after the first attempt (default 3)
        retry_delays: Waits before each retry in seconds (default ``RETRY_DELAYS``)

    Example:
        >>> async with AsyncINEClient(language="EN") as client:
        ...     response = await client._make_request("/endpoint", {"param": "value"})
    """

    BASE_URL = "https://www.ine.pt"
    DEFAULT_TIMEOUT = retry.DEFAULT_TIMEOUT
    MAX_RETRIES = retry.MAX_RETRIES
    RETRY_DELAYS = retry.RETRY_DELAYS
    USER_AGENT = f"pyptine/{__version__} (Python INE API Client - Async)"

    def __init__(
        self,
        language: str = "EN",
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = MAX_RETRIES,
        retry_delays: Sequence[float] | None = None,
    ) -> None:
        """Initialize async INE client."""
        self.language = language.upper()
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delays = retry_delays
        self.client: httpx.AsyncClient | None = None

        # Validate language
        if self.language not in ("EN", "PT"):
            raise ValueError(f"Language must be 'EN' or 'PT', got: {language}")

        logger.info(
            f"Initialized async INE client (language={self.language}, "
            f"timeout={self.timeout}s, max_retries={self.max_retries})"
        )

    async def __aenter__(self) -> "AsyncINEClient":
        """Async context manager entry."""
        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": self.USER_AGENT,
                "Accept": "application/json, text/xml",
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=httpx.Timeout(self.timeout, connect=retry.connect_timeout(self.timeout)),
        )
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        if self.client:
            await self.client.aclose()
            logger.debug("Closed async HTTP client")

    def _waits(self) -> list[float]:
        delays = self.RETRY_DELAYS if self.retry_delays is None else self.retry_delays
        return retry.waits(delays, self.max_retries)

    async def _make_request(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        response_format: str = "json",
    ) -> dict[str, Any] | str:
        """Make an async HTTP request to INE API, retrying INE's transient failures.

        Raises:
            INERefusalError: If INE refused the request (not retried)
            RateLimitError: If INE still answers 429 after the retries
            APIError: If the request still fails after the retries, on a read timeout, or on
                an HTTP error that is not retried
        """
        if not self.client:
            raise RuntimeError("Client not initialized. Use 'async with' context manager.")
        if response_format not in ("json", "xml"):
            raise ValueError(f"Unsupported response format: {response_format}")

        url = self.BASE_URL + endpoint
        params = {} if params is None else params.copy()
        params["lang"] = self.language
        logger.debug(f"Making async request to {endpoint} with params: {params}")

        waits = self._waits()
        for attempt in range(len(waits) + 1):
            try:
                return await self._attempt(url, params, response_format, len(waits) + 1)
            except retry.RetryableError as e:
                if attempt == len(waits):
                    raise e.error from None
                wait = retry.wait_for(e.status, e.headers, waits[attempt])
                logger.warning(
                    f"INE request failed ({e}); retry {attempt + 1} of {len(waits)} in {wait:g} s"
                )
                await asyncio.sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _attempt(
        self, url: str, params: dict[str, Any], response_format: str, attempts: int
    ) -> dict[str, Any] | str:
        """One request. Raises retry.RetryableError for a failure worth repeating."""
        assert self.client is not None
        start_time = time.time()
        try:
            response = await self.client.get(url, params=params)
        except httpx.ConnectTimeout as e:
            raise retry.RetryableError(APIError(0, f"Network error: {e}")) from e
        except httpx.TimeoutException:
            logger.error(f"Async read timeout after {self.timeout}s for {url}")
            raise APIError(0, retry.read_timeout_message(self.timeout)) from None
        except httpx.TransportError as e:
            raise retry.RetryableError(APIError(0, f"Network error: {e}")) from e
        logger.debug(
            f"Async request completed in {time.time() - start_time:.2f}s "
            f"(status={response.status_code})"
        )

        status = response.status_code
        if status in retry.RETRY_STATUS:
            text = retry.status_message(status, attempts)
            error = (
                RateLimitError(f"Too many requests to INE API: {text}")
                if status == 429
                else (APIError(status, text))
            )
            raise retry.RetryableError(error, status=status, headers=response.headers)
        if status >= 400:
            raise APIError(status, f"HTTP {status} for {response.url}")

        if response_format == "xml":
            check_catalogue_body(response.content, str(params.get("varcd", "")), str(response.url))
        try:
            if response_format == "json":
                body: dict[str, Any] | str = self._parse_json_response(response)
            else:
                body = self._parse_xml_response(response)
                ET.fromstring(body)
        except (APIError, ET.ParseError) as e:
            error = e if isinstance(e, APIError) else APIError(0, f"Invalid XML response: {e}")
            raise retry.RetryableError(error) from e

        # INE refuses with HTTP 200 and Sucesso.Falso; raise (not retried) instead of returning.
        if response_format == "json":
            check_success(body, str(params.get("varcd", "")), str(response.url))
        return body

    def _parse_json_response(self, response: httpx.Response) -> dict[str, Any]:
        """Parse JSON response.

        Raises:
            APIError: If JSON parsing fails
        """
        try:
            data: dict[str, Any] = response.json()
            logger.debug("Parsed JSON response of %d bytes", len(response.content))
            return data
        except ValueError as e:
            logger.error(f"Failed to parse JSON response: {str(e)}")
            raise APIError(0, f"Invalid JSON response: {str(e)}") from e

    def _parse_xml_response(self, response: httpx.Response) -> str:
        """Return the XML text of a response.

        Raises:
            APIError: If response is not valid text
        """
        try:
            xml_text = response.text
            logger.debug(f"Parsed XML response with {len(xml_text)} characters")
            return xml_text
        except Exception as e:
            logger.error(f"Failed to parse XML response: {str(e)}")
            raise APIError(0, f"Invalid XML response: {str(e)}") from e
