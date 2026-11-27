"""Metadata client for INE Portugal API."""

import logging
from datetime import datetime
from typing import Any, Union, cast

from pyptine.client.base import INEClient
from pyptine.models.indicator import (
    Dimension,
    DimensionValue,
    Indicator,
    IndicatorMetadata,
)
from pyptine.utils.exceptions import DataProcessingError

logger = logging.getLogger(__name__)


class MetadataClient(INEClient):
    """Client for INE metadata API endpoint.

    Fetches and parses indicator metadata including dimensions and their values.

    Example:
        >>> client = MetadataClient(language="EN")
        >>> metadata = client.get_metadata("0004167")
        >>> print(metadata.title)
        'Resident population'
    """

    METADATA_ENDPOINT = "/ine/json_indicador/pindicaMeta.jsp"

    def get_metadata(self, varcd: str) -> IndicatorMetadata:
        """Get complete metadata for an indicator.

        Args:
            varcd: Indicator code (e.g., "0004167")

        Returns:
            IndicatorMetadata object with dimensions and other info

        Raises:
            InvalidIndicatorError: If indicator not found
            DataProcessingError: If response parsing fails

        Example:
            >>> client = MetadataClient()
            >>> metadata = client.get_metadata("0004167")
            >>> for dim in metadata.dimensions:
            ...     print(f"{dim.name}: {len(dim.values)} values")
        """
        logger.info(f"Fetching metadata for indicator {varcd}")

        params = {"varcd": varcd}

        try:
            response = self._make_request(
                self.METADATA_ENDPOINT, params=params, response_format="json"
            )

            # Parse response
            metadata = self._parse_metadata_response(cast(dict[str, Any], response))

            logger.info(f"Retrieved metadata for {varcd}: {len(metadata.dimensions)} dimensions")

            return metadata

        except Exception as e:
            logger.error(f"Failed to get metadata for {varcd}: {str(e)}")
            raise

    def get_dimensions(self, varcd: str) -> list[Dimension]:
        """Get dimension definitions for an indicator.

        Args:
            varcd: Indicator code

        Returns:
            List of Dimension objects

        Example:
            >>> client = MetadataClient()
            >>> dimensions = client.get_dimensions("0004167")
            >>> for dim in dimensions:
            ...     print(f"Dimension {dim.id}: {dim.name}")
        """
        metadata = self.get_metadata(varcd)
        return metadata.dimensions

    def get_dimension_values(self, varcd: str, dimension_id: int) -> list[DimensionValue]:
        """Get available values for a specific dimension.

        Args:
            varcd: Indicator code
            dimension_id: Dimension identifier (1, 2, 3, etc.)

        Returns:
            List of DimensionValue objects

        Raises:
            ValueError: If dimension_id not found

        Example:
            >>> client = MetadataClient()
            >>> values = client.get_dimension_values("0004167", 1)
            >>> for value in values:
            ...     print(f"{value.code}: {value.label}")
        """
        dimensions = self.get_dimensions(varcd)

        for dim in dimensions:
            if dim.id == dimension_id:
                return dim.values

        raise ValueError(
            f"Dimension {dimension_id} not found for indicator {varcd}. "
            f"Available dimensions: {[d.id for d in dimensions]}"
        )

    def _parse_metadata_response(
        self, response: Union[dict[str, Any], list[Any]]
    ) -> IndicatorMetadata:
        """Parse metadata API response into IndicatorMetadata model.

        Args:
            response: Raw JSON response from API (can be dict or list)

        Returns:
            Parsed IndicatorMetadata object

        Raises:
            DataProcessingError: If parsing fails
        """
        try:
            if isinstance(response, list):
                if len(response) == 1 and isinstance(response[0], dict):
                    # If it's a list with a single dictionary, use that dictionary
                    response = response[0]
                elif not response:
                    # If it's an empty list, treat as no metadata found
                    raise DataProcessingError(
                        "No metadata found for indicator (empty list response)."
                    )
                else:
                    # If it's a list with multiple items or non-dict items, it's unexpected
                    raise DataProcessingError(
                        f"Unexpected metadata API response format: received a list with {len(response)} items. "
                        f"Expected a single dictionary for indicator metadata."
                    )

            if isinstance(response, dict):
                varcd = response.get("IndicadorCod") or ""
                title = response.get("IndicadorNome") or ""
                language = response.get("Lingua") or self.language
                unit = response.get("UnidadeMedida")
                # pindicaMeta sends no source, notes, description, theme, subtheme,
                # geographic level or URLs (checked 2026-11-27; the drift check watches
                # UNSENT_META). Those come from the catalogue: see with_catalogue.
                periodicity = response.get("Periodic")
                last_period = response.get("UltimoPeriodo")

                last_update_str = response.get("DataUltimaAtualizacao")
                last_update = None
                if last_update_str:
                    try:  # INE sends YYYY-MM-DD
                        last_update = datetime.strptime(last_update_str, "%Y-%m-%d")
                    except ValueError:
                        logger.warning(f"Could not parse last_update: {last_update_str}")

                if not isinstance(response.get("Dimensoes"), dict):
                    raise DataProcessingError(
                        f"INE's metadata for {varcd or 'the indicator'} has no Dimensoes "
                        f"object (keys: {sorted(response)})"
                    )
                dimensions = self._parse_dimensions(response["Dimensoes"])

                return IndicatorMetadata(
                    varcd=varcd,
                    title=title,
                    language=language,
                    dimensions=dimensions,
                    unit=unit,
                    periodicity=periodicity,
                    last_period=last_period,
                    last_update=last_update,
                )
            else:
                # This case should ideally not be reached after the list check
                raise DataProcessingError(
                    "Unexpected metadata API response format: not a dictionary after list check."
                )

        except Exception as e:
            logger.error(f"Failed to parse metadata response: {str(e)}")
            raise DataProcessingError(f"Failed to parse metadata: {str(e)}") from e

    def _parse_dimensions(self, dims_data: dict[str, Any]) -> list[Dimension]:
        """Parse INE's ``Dimensoes`` object.

        Structure:
        {
            "Descricao_Dim": [
                {"dim_num": "1", "abrv": "Name", ...},
                {"dim_num": "2", "abrv": "Name2", ...}
            ],
            "Categoria_Dim": [
                {"Dim_Num1_CODE1": [{"categ_cod": "CODE1", "categ_dsg": "Label1", ...}]},
                {"Dim_Num2_CODE2": [{"categ_cod": "CODE2", "categ_dsg": "Label2", ...}]}
            ]
        }

        Args:
            dims_data: INE's ``Dimensoes``

        Returns:
            List of parsed Dimension objects
        """
        dimensions = []

        # Get dimension descriptions
        dim_descriptions = dims_data.get("Descricao_Dim", [])
        dim_categories = dims_data.get("Categoria_Dim", [])

        # Build a map of dimension info
        dim_info_map = {}
        for dim_desc in dim_descriptions:
            dim_num = dim_desc.get("dim_num", "")
            dim_info_map[dim_num] = {
                "name": dim_desc.get("abrv", f"Dimension {dim_num}"),
                "description": dim_desc.get("nota_dsg"),
            }

        # Parse dimension values from categories
        dim_values_map: dict[str, list[DimensionValue]] = {}

        # Flatten all category items
        if isinstance(dim_categories, list):
            for cat_item in dim_categories:
                if isinstance(cat_item, dict):
                    # Each item has keys like "Dim_Num1_S7A2011"
                    for key, value_list in cat_item.items():
                        # Extract dimension number from key (e.g., "Dim_Num1_..." -> "1")
                        if key.startswith("Dim_Num"):
                            parts = key.split("_")
                            if len(parts) >= 2:
                                dim_num = parts[1].replace("Num", "")

                                if dim_num not in dim_values_map:
                                    dim_values_map[dim_num] = []

                                # Parse values
                                if isinstance(value_list, list):
                                    for val_data in value_list:
                                        if isinstance(val_data, dict):
                                            # The filter takes cat_id, not categ_cod
                                            # (0012234 Dim3: "H1" accepted, "H.1" refused).
                                            if not val_data.get("cat_id"):
                                                raise DataProcessingError(
                                                    f"Category without cat_id in {key}: "
                                                    f"{val_data!r}"
                                                )
                                            value = DimensionValue(
                                                code=val_data["cat_id"],
                                                label=val_data.get("categ_dsg", ""),
                                                order=val_data.get("categ_ord"),
                                                categ_cod=val_data.get("categ_cod"),
                                            )
                                            dim_values_map[dim_num].append(value)

        # Build Dimension objects
        for dim_num, info in dim_info_map.items():
            values = dim_values_map.get(dim_num, [])
            dimension = Dimension(
                id=int(dim_num) if dim_num.isdigit() else 0,
                name=info["name"],
                description=info.get("description"),
                values=values,
            )
            dimensions.append(dimension)

        # Sort by dimension ID
        dimensions.sort(key=lambda d: d.id)

        return dimensions


#: Fields of IndicatorMetadata that only INE's catalogue (opc=1) sends.
CATALOGUE_FIELDS = (
    "description",
    "theme",
    "subtheme",
    "keywords",
    "source",
    "geo_last_level",
    "html_url",
    "metadata_url",
    "data_url",
)


def with_catalogue(metadata: IndicatorMetadata, indicator: Indicator) -> IndicatorMetadata:
    """Metadata with the fields only the catalogue sends filled from its ``opc=1`` record.

    Title, unit, periodicity, last period and last update stay the metadata endpoint's.

    Raises:
        DataProcessingError: If the two records are for different indicators
    """
    if indicator.varcd != metadata.varcd:
        raise DataProcessingError(
            f"Catalogue record {indicator.varcd} does not match metadata {metadata.varcd}"
        )
    return metadata.model_copy(update={f: getattr(indicator, f) for f in CATALOGUE_FIELDS})
