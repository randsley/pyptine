"""Async data client for INE Portugal API."""

import logging
import warnings
from collections.abc import AsyncIterator
from typing import Any, Optional, Union, cast

from pyptine.client import parsing
from pyptine.client.async_base import AsyncINEClient
from pyptine.client.metadata import MetadataClient
from pyptine.client.parsing import DimensionFilters
from pyptine.models.indicator import IndicatorMetadata
from pyptine.models.response import DataResponse

logger = logging.getLogger(__name__)


class AsyncDataClient(AsyncINEClient):
    """Async client for INE data API endpoint.

    Same parsing and validation as :class:`pyptine.client.data.DataClient`: rows carry
    ``period`` and ``period_code``, INE refusals raise ``INERefusalError``, and without ``Dim1``
    INE returns only the latest period. Metadata is fetched through a (synchronous, cached)
    MetadataClient.

    Example:
        >>> async with AsyncDataClient(language="EN") as client:
        ...     response = await client.get_data("0008273", {"Dim1": ["S7A2021", "S7A2022"]})
        ...     df = response.to_dataframe()
    """

    DATA_ENDPOINT = "/ine/json_indicador/pindica.jsp"
    DEFAULT_PERIODS_PER_REQUEST = 10

    def __init__(
        self,
        language: str = "EN",
        timeout: int = AsyncINEClient.DEFAULT_TIMEOUT,
        metadata_client: Optional[MetadataClient] = None,
    ):
        super().__init__(language, timeout)
        if metadata_client is None:
            metadata_client = MetadataClient(language=self.language, timeout=timeout)
        elif getattr(metadata_client, "language", self.language) != self.language:
            raise ValueError(
                f"metadata_client language {metadata_client.language} differs from the data "
                f"client's {self.language}"
            )
        self.metadata_client = metadata_client

    def _metadata(self, varcd: str) -> IndicatorMetadata:
        return self.metadata_client.get_metadata(varcd)

    async def get_data(
        self,
        varcd: str,
        dimensions: Optional[DimensionFilters] = None,
    ) -> DataResponse:
        """Fetch indicator data asynchronously (see ``DataClient.get_data``)."""
        logger.info(f"Fetching data asynchronously for indicator {varcd}")
        params = self._build_params(varcd, dimensions)
        raw_response = await self._make_request(
            self.DATA_ENDPOINT, params=params, response_format="json"
        )
        data_response = self._parse_data_response(
            varcd, cast(Union[dict[str, Any], list[dict[str, Any]]], raw_response)
        )
        logger.info(f"Retrieved {len(data_response.data)} data points for {varcd}")
        return data_response

    async def get_all_data(
        self,
        varcd: str,
        dimensions: Optional[DimensionFilters] = None,
        periods_per_request: int = DEFAULT_PERIODS_PER_REQUEST,
        chunk_size: Optional[int] = None,
    ) -> AsyncIterator[DataResponse]:
        """Fetch every period, a batch of periods per request (see ``DataClient.get_all_data``).

        ``chunk_size`` is deprecated and ignored: INE does not paginate rows.
        """
        if chunk_size is not None:
            warnings.warn(
                "chunk_size is ignored: INE does not paginate rows. get_all_data now fetches "
                "periods in batches (periods_per_request).",
                DeprecationWarning,
                stacklevel=2,
            )
        if periods_per_request < 1:
            raise ValueError(f"periods_per_request must be at least 1, got {periods_per_request}")
        dims: DimensionFilters = dict(dimensions or {})
        if "Dim1" in dims:
            periods = parsing.split_codes(dims["Dim1"])
        else:
            periods = parsing.period_codes(self._metadata(varcd))
        for i in range(0, len(periods), periods_per_request):
            yield await self.get_data(varcd, {**dims, "Dim1": periods[i : i + periods_per_request]})

    def _build_params(
        self,
        varcd: str,
        dimensions: Optional[DimensionFilters] = None,
    ) -> dict[str, str]:
        """Build query parameters for a data request, validating the dimension filters."""
        params = {"op": "2", "varcd": varcd}
        if dimensions:
            params.update(parsing.normalise_dimensions(varcd, dimensions, self._metadata(varcd)))
        return params

    def _parse_data_response(
        self, varcd: str, response: Union[dict[str, Any], list[dict[str, Any]]]
    ) -> DataResponse:
        """Parse a data API response (see :func:`pyptine.client.parsing.parse_data_response`)."""
        return parsing.parse_data_response(
            varcd, response, self.language, lambda: self._metadata(varcd)
        )

    def _process_data_point(self, data_point: dict[str, Any]) -> dict[str, Any]:
        """Process a single data row."""
        return parsing.process_data_point(data_point)

    def validate_dimensions(self, varcd: str, dimensions: DimensionFilters) -> bool:
        """Validate dimension filters against indicator metadata (see ``DataClient``)."""
        parsing.normalise_dimensions(varcd, dimensions, self._metadata(varcd))
        return True
