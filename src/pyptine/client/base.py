"""Base HTTP client for INE Portugal API."""

import logging
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import requests
from requests.adapters import HTTPAdapter

from pyptine.__version__ import __version__
from pyptine.cache.disk import DiskCache
from pyptine.client import retry
from pyptine.client.parsing import check_catalogue_body, check_success
from pyptine.client.retry import JSONBody
from pyptine.utils.exceptions import APIError, RateLimitError

logger = logging.getLogger(__name__)


class INEClient:
    """Base client for INE Portugal API.

    Provides core HTTP functionality including:
    - Session management with connection pooling
    - Retries for INE's throttling and load (see :mod:`pyptine.client.retry`): HTTP 404, 429
      and 5xx, failed connections and malformed bodies, after 5, 20 and 60 s
    - A read timeout longer than INE's 120 s gateway limit
    - INE refusals (HTTP 200 + ``Sucesso.Falso``) raised as ``INERefusalError``, never retried

    Args:
        language: Language for API responses ("EN" or "PT")
        timeout: Read timeout in seconds (default 130; INE's gateway answers 504 after 120 s)
        cache_enabled: Enable HTTP caching
        cache_dir: Directory for cache storage
        max_retries: Retries after the first attempt (default 3)
        retry_delays: Waits before each retry in seconds (default ``RETRY_DELAYS``; the last
            value repeats if there are more retries)
        cache_ttl: How long data responses stay cached, in seconds (default 1 day). Metadata
            and catalogue responses stay 7 days.

    Example:
        >>> client = INEClient(language="EN")
        >>> response = client._make_request("/endpoint", {"param": "value"})
    """

    BASE_URL = "https://www.ine.pt"
    DEFAULT_TIMEOUT = retry.DEFAULT_TIMEOUT
    MAX_RETRIES = retry.MAX_RETRIES
    RETRY_DELAYS = retry.RETRY_DELAYS
    USER_AGENT = f"pyptine/{__version__} (Python INE API Client)"

    def __init__(
        self,
        language: str = "EN",
        timeout: int = DEFAULT_TIMEOUT,
        cache_enabled: bool = True,
        cache_dir: Path | None = None,
        max_retries: int = MAX_RETRIES,
        retry_delays: Sequence[float] | None = None,
        cache_ttl: int | None = None,
        cache: DiskCache | None = None,
    ) -> None:
        """Initialize INE client.

        ``cache`` is an existing DiskCache to share (INE gives its four clients one); without
        it, a client opens its own from ``cache_dir`` and ``cache_ttl``.
        """
        self.language = language.upper()
        self.timeout = timeout
        self.cache_enabled = cache_enabled
        self.cache_dir = cache_dir
        self.max_retries = max_retries
        self.retry_delays = retry_delays
        self.cache = None

        # Validate language
        if self.language not in ("EN", "PT"):
            raise ValueError(f"Language must be 'EN' or 'PT', got: {language}")

        # Initialize cache if enabled
        if self.cache_enabled and cache is not None:
            self.cache = cache
        elif self.cache_enabled:
            if cache_ttl is None:
                self.cache = DiskCache(cache_dir=cache_dir)
            else:
                self.cache = DiskCache(cache_dir=cache_dir, data_ttl=cache_ttl)
            logger.debug(f"Cache enabled at: {self.cache.get_cache_dir()}")

        # Initialize session
        self.session = self._create_session()

        logger.info(
            f"Initialized INE client (language={self.language}, "
            f"cache={self.cache_enabled}, timeout={self.timeout}s)"
        )

    def _create_session(self) -> requests.Session:
        """Create the HTTP session (retries are done by :meth:`_make_request`, not urllib3)."""
        session = requests.Session()
        adapter = HTTPAdapter(max_retries=0, pool_connections=10, pool_maxsize=20)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update(
            {
                "User-Agent": self.USER_AGENT,
                "Accept": "application/json, text/xml",
                "Accept-Encoding": "gzip, deflate",
            }
        )
        return session

    def _get_session_for_endpoint(self, endpoint: str) -> requests.Session:
        """Get appropriate session for endpoint.

        Args:
            endpoint: API endpoint path

        Returns:
            Cached or regular session based on endpoint and cache settings
        """
        if not self.cache_enabled or self.cache is None:
            return self.session

        # Use data cache for data endpoint
        if "pindica.jsp" in endpoint:
            return self.cache.get_data_session()

        # Use metadata cache for metadata and catalogue endpoints
        elif "pindicaMeta.jsp" in endpoint or "xml_indic.jsp" in endpoint:
            return self.cache.get_metadata_session()

        # Default to regular session for unknown endpoints
        else:
            return self.session

    def _waits(self) -> list[float]:
        delays = self.RETRY_DELAYS if self.retry_delays is None else self.retry_delays
        return retry.waits(delays, self.max_retries)

    def _make_request(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        response_format: str = "json",
        progress_callback: Any | None = None,
    ) -> JSONBody | str:
        """Make HTTP request to INE API, retrying INE's transient failures.

        Args:
            endpoint: API endpoint path (e.g., "/ine/json_indicador/pindica.jsp")
            params: Query parameters
            response_format: Expected response format ("json" or "xml")
            progress_callback: Optional callback function(downloaded_bytes, total_bytes)

        Returns:
            Parsed JSON dict or raw XML string

        Raises:
            INERefusalError: If INE refused the request (not retried)
            RateLimitError: If INE still answers 429 after the retries
            APIError: If the request still fails after the retries, on a read timeout, or on
                an HTTP error that is not retried
        """
        if response_format not in ("json", "xml"):
            raise ValueError(f"Unsupported response format: {response_format}")

        url = self.BASE_URL + endpoint
        params = {} if params is None else params.copy()
        params["lang"] = self.language
        logger.debug(f"Making request to {endpoint} with params: {params}")
        session = self._get_session_for_endpoint(endpoint)

        waits = self._waits()
        for attempt in range(len(waits) + 1):
            try:
                return self._attempt(session, url, params, response_format, progress_callback)
            except retry.RetryableError as e:
                if attempt == len(waits):
                    raise e.error from None
                wait = retry.wait_for(e.status, e.headers, waits[attempt])
                logger.warning(
                    f"INE request failed ({e}); retry {attempt + 1} of {len(waits)} in {wait:g} s"
                )
                time.sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    def _attempt(
        self,
        session: requests.Session,
        url: str,
        params: dict[str, Any],
        response_format: str,
        progress_callback: Any | None,
    ) -> JSONBody | str:
        """One request. Raises retry.RetryableError for a failure worth repeating."""
        timeout = (retry.connect_timeout(self.timeout), self.timeout)
        attempts = len(self._waits()) + 1
        start_time = time.time()
        try:
            if progress_callback is not None:
                # Stream through the uncached session to report progress.
                response = self.session.get(url, params=params, timeout=timeout, stream=True)
                self._check_status(response, attempts)
                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0
                chunks = []
                progress_callback(0, total_size)
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        chunks.append(chunk)
                        downloaded += len(chunk)
                        progress_callback(downloaded, total_size)
                response._content = b"".join(chunks)
            else:
                response = session.get(url, params=params, timeout=timeout)
                self._check_status(response, attempts)
        except requests.ReadTimeout:
            logger.error(f"Read timeout after {self.timeout}s for {url}")
            raise APIError(0, retry.read_timeout_message(self.timeout)) from None
        except (requests.ConnectionError, requests.exceptions.ChunkedEncodingError) as e:
            raise retry.RetryableError(APIError(0, f"Network error: {e}")) from e
        except requests.Timeout:
            raise APIError(0, retry.read_timeout_message(self.timeout)) from None

        from_cache = getattr(response, "from_cache", False)
        logger.debug(
            f"Request completed in {time.time() - start_time:.2f}s "
            f"({'from cache' if from_cache else 'from API'}, status={response.status_code})"
        )

        if response_format == "xml":
            # INE's Java error page for a code not in the catalogue: refused, not retried.
            check_catalogue_body(response.content, str(params.get("varcd", "")), response.url)
        try:
            if response_format == "json":
                body: JSONBody | str = self._parse_json_response(response)
            else:
                body = self._parse_xml_response(response)
                ET.fromstring(body)  # a truncated catalogue must not be returned or cached
        except (APIError, ET.ParseError) as e:
            self._forget(session, response)
            error = e if isinstance(e, APIError) else APIError(0, f"Invalid XML response: {e}")
            raise retry.RetryableError(error) from e

        # INE refuses with HTTP 200 and Sucesso.Falso; raise (not retried) instead of returning.
        if response_format == "json":
            check_success(body, str(params.get("varcd", "")), response.url)
        return body

    def _check_status(self, response: requests.Response, attempts: int) -> None:
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

    @staticmethod
    def _forget(session: requests.Session, response: requests.Response) -> None:
        """Drop a response from the cache (a malformed body must not be served again)."""
        cache = getattr(session, "cache", None)
        if cache is not None:
            try:
                cache.delete(requests=[response.request])
            except Exception as e:  # the retry still happens; the stale entry would expire
                logger.warning(f"Could not drop a malformed response from the cache: {e}")

    def _parse_json_response(self, response: requests.Response) -> JSONBody:
        """Parse JSON response.

        Raises:
            APIError: If JSON parsing fails
        """
        try:
            data: JSONBody = response.json()
            logger.debug("Parsed JSON response of %d bytes", len(response.content))
            return data
        except ValueError as e:
            logger.error(f"Failed to parse JSON response: {str(e)}")
            raise APIError(0, f"Invalid JSON response: {str(e)}") from e

    def _parse_xml_response(self, response: requests.Response) -> str:
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

    def is_catalogue_cached_on_disk(self) -> bool:
        """Check if catalogue data is cached on disk.

        Returns:
            True if catalogue is in disk cache
        """
        if not self.cache_enabled or self.cache is None:
            return False
        return self.cache.has_catalogue_cached()

    def close(self) -> None:
        """Close HTTP session and cleanup resources.

        Should be called when the client is no longer needed.
        """
        if self.session:
            self.session.close()
            logger.debug("Closed HTTP session")

        if self.cache:
            self.cache.close()
            logger.debug("Closed cache sessions")

    def __enter__(self) -> "INEClient":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.close()
