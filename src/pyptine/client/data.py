"""Data client for INE Portugal API."""

import logging
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

from pyptine.cache.disk import DiskCache
from pyptine.client import parsing
from pyptine.client.base import INEClient
from pyptine.client.metadata import MetadataClient
from pyptine.client.parsing import DimensionFilters
from pyptine.models.indicator import IndicatorMetadata
from pyptine.models.response import DataResponse
from pyptine.provenance import request_url

logger = logging.getLogger(__name__)


class DataClient(INEClient):
    """Client for INE data API endpoint.

    Fetches and parses indicator data. Every row carries ``period`` (INE's label, e.g. "2022")
    and ``period_code`` (the code used to filter, e.g. "S7A2022").

    INE returns only the **latest period** when no period (``Dim1``) is given. Ask for several
    periods with a list or a comma-separated ``Dim1``, or use :meth:`get_all_data`.

    Example:
        >>> client = DataClient(language="EN")
        >>> response = client.get_data("0008273", dimensions={"Dim1": ["S7A2021", "S7A2022"]})
        >>> df = response.to_dataframe()
    """

    DATA_ENDPOINT = "/ine/json_indicador/pindica.jsp"
    DEFAULT_PERIODS_PER_REQUEST = 10

    def __init__(
        self,
        language: str = "EN",
        timeout: int = INEClient.DEFAULT_TIMEOUT,
        cache_enabled: bool = True,
        cache_dir: Path | None = None,
        metadata_client: MetadataClient | None = None,
        cache_ttl: int | None = None,
        cache: DiskCache | None = None,
    ):
        super().__init__(
            language, timeout, cache_enabled, cache_dir, cache_ttl=cache_ttl, cache=cache
        )
        if metadata_client is None:
            metadata_client = MetadataClient(
                language, timeout, cache_enabled, cache_dir, cache=self.cache
            )
        elif getattr(metadata_client, "language", self.language) != self.language:
            # Period labels are matched to codes through the metadata, in the same language.
            raise ValueError(
                f"metadata_client language {metadata_client.language} differs from the data "
                f"client's {self.language}"
            )
        self.metadata_client = metadata_client

    def _metadata(self, varcd: str) -> IndicatorMetadata:
        return self.metadata_client.get_metadata(varcd)

    def get_data(
        self,
        varcd: str,
        dimensions: DimensionFilters | None = None,
    ) -> DataResponse:
        """Fetch indicator data with optional dimension filters.

        Args:
            varcd: Indicator code (e.g., "0008273")
            dimensions: Optional filters by dimension. Each value is one code, a list of codes
                or a comma-separated string. Codes are ``DimensionValue.code`` from
                :meth:`MetadataClient.get_dimensions` (INE's cat_id), e.g.
                ``{"Dim1": ["S7A2021", "S7A2022"], "Dim2": "PT"}``. Without ``Dim1``, INE
                returns only the latest period.

        Returns:
            DataResponse whose rows carry ``period``, ``period_code`` and ``value``

        Raises:
            DimensionError: If a dimension key or code is not valid for the indicator
            INERefusalError: If INE refused the request
            DataProcessingError: If the response cannot be parsed
        """
        logger.info(f"Fetching data for indicator {varcd}")
        params = self._build_params(varcd, dimensions)
        raw_response = self._make_request(self.DATA_ENDPOINT, params=params, response_format="json")
        data_response = self._parse_data_response(
            varcd, cast(dict[str, Any] | list[dict[str, Any]], raw_response), params
        )
        logger.info(f"Retrieved {len(data_response.data)} data points for {varcd}")
        return data_response

    def get_all_data(
        self,
        varcd: str,
        dimensions: DimensionFilters | None = None,
        periods_per_request: int = DEFAULT_PERIODS_PER_REQUEST,
        chunk_size: int | None = None,
    ) -> Iterator[DataResponse]:
        """Fetch every period of an indicator, a batch of periods per request.

        INE has no row pagination (it ignores ``start``/``count``) and returns only the latest
        period when no period is given. This method asks for the periods themselves: all of
        the indicator's periods, or those given in ``dimensions["Dim1"]``, in batches of
        ``periods_per_request`` (one comma-separated ``Dim1`` per request).

        Args:
            varcd: Indicator code
            dimensions: Optional filters, as for :meth:`get_data`
            periods_per_request: Periods per request (lower it for indicators with many rows
                per period; INE's gateway gives up after 120 s)
            chunk_size: Deprecated and ignored (INE does not paginate rows)

        Yields:
            One DataResponse per batch of periods, oldest first

        Example:
            >>> client = DataClient()
            >>> chunks = list(client.get_all_data("0008273", dimensions={"Dim2": "PT"}))
            >>> rows = [row for chunk in chunks for row in chunk.data]
        """
        if chunk_size is not None:
            warnings.warn(
                "chunk_size is ignored: INE does not paginate rows. get_all_data now fetches "
                "periods in batches (periods_per_request).",
                DeprecationWarning,
                stacklevel=2,
            )
        for batch_dims in self._period_batches(varcd, dimensions, periods_per_request):
            yield self.get_data(varcd, batch_dims)

    def _period_batches(
        self,
        varcd: str,
        dimensions: DimensionFilters | None,
        periods_per_request: int,
    ) -> Iterator[DimensionFilters]:
        if periods_per_request < 1:
            raise ValueError(f"periods_per_request must be at least 1, got {periods_per_request}")
        dims: DimensionFilters = dict(dimensions or {})
        if "Dim1" in dims:
            periods = parsing.split_codes(dims["Dim1"])
        else:
            periods = parsing.period_codes(self._metadata(varcd))
        for i in range(0, len(periods), periods_per_request):
            yield {**dims, "Dim1": periods[i : i + periods_per_request]}

    def _build_params(
        self,
        varcd: str,
        dimensions: DimensionFilters | None = None,
    ) -> dict[str, str]:
        """Build query parameters for a data request, validating the dimension filters.

        Raises:
            DimensionError: If dimension keys or codes are invalid
        """
        params = {"op": "2", "varcd": varcd}
        if dimensions:
            params.update(parsing.normalise_dimensions(varcd, dimensions, self._metadata(varcd)))
        return params

    def _parse_data_response(
        self,
        varcd: str,
        response: dict[str, Any] | list[dict[str, Any]],
        params: dict[str, str] | None = None,
    ) -> DataResponse:
        """Parse a data API response (see :func:`pyptine.client.parsing.parse_data_response`).

        ``params`` (the request's query, without ``lang``) is recorded as provenance.
        """
        url = dims = None
        if params is not None:
            url = request_url(self.BASE_URL, self.DATA_ENDPOINT, {**params, "lang": self.language})
            dims = {k: v for k, v in params.items() if k.startswith("Dim")}
        return parsing.parse_data_response(
            varcd, response, self.language, lambda: self._metadata(varcd), url, dims
        )

    def _process_data_point(self, data_point: dict[str, Any]) -> dict[str, Any]:
        """Process a single data row (see :func:`pyptine.client.parsing.process_data_point`)."""
        return parsing.process_data_point(data_point)

    def validate_dimensions(self, varcd: str, dimensions: DimensionFilters) -> bool:
        """Validate dimension filters against indicator metadata.

        Codes are INE's cat_id (``DimensionValue.code``); a value may be a list or a
        comma-separated string of codes.

        Returns:
            True if dimensions are valid

        Raises:
            DimensionError: If any dimension key or code is invalid. The message names the
                code to use when the given one is a label or a categ_cod.
        """
        parsing.normalise_dimensions(varcd, dimensions, self._metadata(varcd))
        return True
