from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, computed_field

from pyptine.models.indicator import Indicator
from pyptine.processors.csv import export_to_csv
from pyptine.processors.json import export_to_json
from pyptine.provenance import INE_ATTRIBUTION, INE_LICENCE, INE_SOURCE, ine_citation

try:
    import pandas as pd

    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False


class DataPoint(BaseModel):
    """Single data point from INE API.

    Represents one observation with its dimension values and the measured value.
    """

    value: Optional[float] = Field(None, description="Measured value")
    dimensions: dict[str, str] = Field(default_factory=dict, description="Dimension values")
    unit: Optional[str] = Field(None, description="Unit of measurement")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "value": 10295909.0,
                "dimensions": {"Period": "2021", "Geographic localization": "Portugal"},
                "unit": "No.",
            }
        }
    )


class DataResponse(BaseModel):
    """Indicator data with its provenance.

    ``extraction_date`` is INE's own extraction timestamp (``DataExtracao``), so a response
    served from the cache keeps the date INE produced it. ``url`` and ``dimensions`` record
    the request; ``citation`` is the sentence INE's reuse terms require. All of it is written
    by :meth:`to_csv` (header) and :meth:`to_json`.
    """

    varcd: str = Field(..., description="Indicator code")
    title: str = Field(..., description="Indicator name")
    language: str = Field(..., description="Language (PT or EN)")
    data: list[dict[str, Any]] = Field(default_factory=list, description="Raw data points")
    unit: Optional[str] = Field(None, description="Unit of measurement")
    extraction_date: Optional[datetime] = Field(
        None, description="INE's extraction timestamp (DataExtracao, UTC)"
    )
    last_updated: Optional[str] = Field(
        None, description="INE's date of the indicator's last update (DataUltimoAtualizacao)"
    )
    metadata_url: Optional[str] = Field(None, description="INE's metadata page (MetaInfUrl)")
    url: Optional[str] = Field(None, description="The data request's URL")
    dimensions: dict[str, str] = Field(
        default_factory=dict, description="Dimension filters sent (cat_id codes, comma-joined)"
    )
    geo_classification: Optional[str] = Field(
        default=None,
        description="Classification of the geography (Dim2): 'nuts' or 'district', from the "
        "codes of its metadata (see pyptine.geography.classify_geography)",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def period_codes(self) -> list[str]:
        """INE period codes in the data, in chronological order."""
        return sorted({str(r["period_code"]) for r in self.data if "period_code" in r})

    @computed_field  # type: ignore[prop-decorator]
    @property
    def periods(self) -> list[str]:
        """INE period labels in the data, in chronological order (of their codes)."""
        labels: dict[str, str] = {}
        for r in self.data:
            if "period_code" in r:
                labels.setdefault(str(r["period_code"]), str(r.get("period", "")))
        return [labels[c] or c for c in sorted(labels)]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def source(self) -> str:
        return INE_SOURCE

    @computed_field  # type: ignore[prop-decorator]
    @property
    def licence(self) -> str:
        return INE_LICENCE

    @computed_field  # type: ignore[prop-decorator]
    @property
    def citation(self) -> str:
        """INE's required citation, with codes, periods and extraction date (if known)."""
        if self.extraction_date is None:
            return INE_ATTRIBUTION
        return ine_citation(self)

    def provenance(self) -> dict[str, Any]:
        """Where the data came from, as written in the CSV header."""
        return {
            "source": self.source,
            "licence": self.licence,
            "citation": self.citation,
            "indicator": self.varcd,
            "title": self.title,
            "unit": self.unit,
            "language": self.language,
            "periods": ", ".join(self.periods) or None,
            "period_codes": ", ".join(self.period_codes) or None,
            "dimensions": "; ".join(f"{k}={v}" for k, v in self.dimensions.items()) or None,
            "url": self.url,
            "metadata_url": self.metadata_url,
            "ine_extraction_date": (
                self.extraction_date.isoformat() if self.extraction_date else None
            ),
            "ine_last_updated": self.last_updated,
        }

    def _chronological(self) -> list[dict[str, Any]]:
        """Rows ordered by period_code (chronological within an indicator), if present."""
        if self.data and all("period_code" in row for row in self.data):
            return sorted(self.data, key=lambda row: str(row["period_code"]))
        return self.data

    def to_dataframe(self) -> "pd.DataFrame":
        """Convert data to pandas DataFrame.

        Returns:
            pandas DataFrame with the indicator data.

        Raises:
            ImportError: If pandas is not installed.

        Example:
            >>> response = DataResponse(...)
            >>> df = response.to_dataframe()
            >>> print(df.head())
        """
        if not PANDAS_AVAILABLE:
            raise ImportError(
                "pandas is required to convert data to DataFrame. "
                "Install it with: pip install pandas"
            )

        if not self.data:
            return pd.DataFrame()

        return pd.DataFrame(self.data)

    def to_csv(
        self,
        filepath: Union[str, Path],
        include_metadata: bool = True,
        **kwargs: Any,
    ) -> None:
        """Export data to CSV file.

        Args:
            filepath: Output file path
            include_metadata: Include metadata as comment header
            **kwargs: Additional arguments passed to df.to_csv()
        """
        export_to_csv(
            self.to_dataframe(),
            Path(filepath),
            include_metadata=include_metadata,
            metadata=self.provenance(),
            **kwargs,
        )

    def to_excel(
        self,
        filepath: Union[str, Path],
        include_metadata: bool = True,
        sheet_name: str = "Data",
    ) -> None:
        """Export data to an Excel file, with a ``Provenance`` sheet (needs openpyxl).

        Args:
            filepath: Output file path (.xlsx)
            include_metadata: Write the Provenance sheet (as the CSV header)
            sheet_name: Name of the data sheet
        """
        from pyptine.processors.excel import export_multiple_sheets

        export_multiple_sheets(
            {sheet_name: self}, Path(filepath), include_metadata=include_metadata
        )

    def to_json(
        self,
        filepath: Union[str, Path],
        pretty: bool = True,
        **kwargs: Any,
    ) -> None:
        """Export data to JSON file.

        Args:
            filepath: Output file path
            pretty: Use pretty printing
            **kwargs: Additional arguments passed to json.dump()
        """
        data = self.model_dump(mode="json")
        export_to_json(data, Path(filepath), pretty=pretty, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        """Convert response to dictionary.

        Returns:
            Dictionary representation of the response.
        """
        return self.model_dump(mode="python")

    def with_geo_level(self, classification: Optional[str] = None) -> "DataResponse":
        """A copy whose rows carry ``geo_level``, read from their ``geocod``.

        Values: ``country``, ``nuts1``, ``nuts2``, ``nuts3``, ``district``, ``municipality``,
        ``parish`` for places; ``abroad``, ``unknown``, ``total`` for INE's pseudo-units. One
        request returns every level at once, so filter on it (or use :meth:`at_level`)
        before aggregating. See :mod:`pyptine.geography` for the rules.

        A two-digit code is a NUTS II region in a NUTS classification but a district in a
        district one (``11`` is Norte, or Lisboa's district), so the classification is
        needed: ``geo_classification``, set from the indicator's metadata when the data is
        fetched, or ``classification`` ("nuts" or "district").

        Raises:
            DataProcessingError: If the classification is not known, a row has no
                ``geocod``, or a code INE is not known to issue (all such codes are named)
        """
        from pyptine.geography import CLASSIFICATIONS, check_codes, level_or_pseudo
        from pyptine.utils.exceptions import DataProcessingError

        scheme = classification or self.geo_classification
        if scheme not in CLASSIFICATIONS:
            raise DataProcessingError(
                f"The geography classification of {self.varcd} is not known "
                f"({scheme!r}); a two-digit code is a NUTS II region or a district depending "
                f"on it. Pass classification= one of {CLASSIFICATIONS}"
            )
        missing = sum(1 for r in self.data if "geocod" not in r)
        if missing:
            raise DataProcessingError(
                f"{missing} row(s) of {self.varcd} have no geocod; cannot assign a level"
            )
        codes = [str(r["geocod"]) for r in self.data]
        check_codes(codes, scheme)
        rows = [{**r, "geo_level": level_or_pseudo(c, scheme)} for r, c in zip(self.data, codes)]
        return self.model_copy(update={"data": rows})

    def at_level(self, *levels: str, classification: Optional[str] = None) -> "DataResponse":
        """A copy with only the rows at the given levels, e.g. ``at_level("municipality")``.

        Levels are those of :meth:`with_geo_level` (places or pseudo-units).

        Raises:
            ValueError: If no level, or an unknown level, is given
            DataProcessingError: As :meth:`with_geo_level`
        """
        from pyptine.geography import LEVELS, PSEUDO_UNITS

        if not levels:
            raise ValueError("at_level needs at least one level")
        unknown = [lv for lv in levels if lv not in LEVELS + PSEUDO_UNITS]
        if unknown:
            raise ValueError(f"Unknown level(s) {unknown}; use {', '.join(LEVELS + PSEUDO_UNITS)}")
        tagged = self.with_geo_level(classification)
        rows = [r for r in tagged.data if r["geo_level"] in levels]
        return self.model_copy(update={"data": rows})

    def calculate_yoy_growth(
        self,
        value_column: str = "value",
        period_column: str = "period_code",
    ) -> "DataResponse":
        """Calculate year-over-year growth rates.

        Compares each period's value to the same period in the previous year.
        Creates a new DataResponse with an added 'yoy_growth' column.

        Args:
            value_column: Name of the column containing values (default: "value")
            period_column: Name of the column containing time period (default: "period_code", INE's period code)

        Returns:
            New DataResponse with YoY growth calculations added

        Raises:
            ImportError: If pandas is not installed

        Example:
            >>> response = ine.get_data("0004167")
            >>> yoy_response = response.calculate_yoy_growth()
            >>> df = yoy_response.to_dataframe()
            >>> print(df[['period', 'value', 'yoy_growth']].head())
        """
        from pyptine.analysis.metrics import calculate_yoy_growth

        new_data = calculate_yoy_growth(self.data, value_column, period_column)
        return self.model_copy(update={"data": new_data})

    def calculate_mom_change(
        self,
        value_column: str = "value",
        period_column: str = "period_code",
    ) -> "DataResponse":
        """Calculate month-over-month percentage changes.

        Compares each period's value to the immediately preceding period.
        Creates a new DataResponse with an added 'mom_change' column.

        Args:
            value_column: Name of the column containing values (default: "value")
            period_column: Name of the column containing time period (default: "period_code", INE's period code)

        Returns:
            New DataResponse with MoM change calculations added

        Raises:
            ImportError: If pandas is not installed

        Example:
            >>> response = ine.get_data("0004127")
            >>> mom_response = response.calculate_mom_change()
            >>> df = mom_response.to_dataframe()
            >>> print(df[['period', 'value', 'mom_change']].head())
        """
        from pyptine.analysis.metrics import calculate_mom_change

        new_data = calculate_mom_change(self.data, value_column, period_column)
        return self.model_copy(update={"data": new_data})

    def calculate_moving_average(
        self,
        window: int = 3,
        value_column: str = "value",
        period_column: str = "period_code",
    ) -> "DataResponse":
        """Calculate simple moving average over a time window.

        Computes the rolling mean of values over a specified window size.
        Creates a new DataResponse with an added 'moving_avg' column.

        Args:
            window: Number of periods to include in the moving average (default: 3)
            value_column: Name of the column containing values (default: "value")
            period_column: Name of the column containing time period (default: "period_code", INE's period code)

        Returns:
            New DataResponse with moving average calculations added

        Raises:
            ImportError: If pandas is not installed
            ValueError: If window size is invalid

        Example:
            >>> response = ine.get_data("0004127")
            >>> ma_response = response.calculate_moving_average(window=12)
            >>> df = ma_response.to_dataframe()
            >>> print(df[['period', 'value', 'moving_avg']].head(15))
        """
        from pyptine.analysis.metrics import calculate_moving_average

        new_data = calculate_moving_average(self.data, window, value_column, period_column)
        return self.model_copy(update={"data": new_data})

    def calculate_exponential_moving_average(
        self,
        span: int = 3,
        value_column: str = "value",
        period_column: str = "period_code",
    ) -> "DataResponse":
        """Calculate exponential moving average.

        Computes the exponentially weighted moving mean, giving more weight to recent values.
        Creates a new DataResponse with an added 'ema' column.

        Args:
            span: Span parameter for EMA calculation (default: 3)
            value_column: Name of the column containing values (default: "value")
            period_column: Name of the column containing time period (default: "period_code", INE's period code)

        Returns:
            New DataResponse with EMA calculations added

        Raises:
            ImportError: If pandas is not installed
            ValueError: If span is invalid

        Example:
            >>> response = ine.get_data("0004127")
            >>> ema_response = response.calculate_exponential_moving_average(span=10)
            >>> df = ema_response.to_dataframe()
            >>> print(df[['period', 'value', 'ema']].head(15))
        """
        from pyptine.analysis.metrics import calculate_exponential_moving_average

        new_data = calculate_exponential_moving_average(
            self.data, span, value_column, period_column
        )
        return self.model_copy(update={"data": new_data})

    def plot(
        self,
        chart_type: str = "line",
        x_column: str = "period",
        y_column: str = "value",
        color_column: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        """Create an interactive visualization of the indicator data.

        Generates an interactive plotly chart from the indicator data. Automatically
        uses the indicator title for the chart title and supports multiple visualization types.

        Args:
            chart_type: Type of chart - "line", "bar", "area", "scatter" (default: "line")
            x_column: Column name for x-axis (default: "period", INE's period label)
            y_column: Column name for y-axis values (default: "value")
            color_column: Optional column to use for coloring by dimension
            **kwargs: Additional arguments passed to plotting functions

        Returns:
            Plotly figure object for display or further customization

        Raises:
            ImportError: If plotly or pandas is not installed
            ValueError: If chart_type is invalid

        Example:
            >>> response = ine.get_data("0004127")
            >>> fig = response.plot(chart_type="line")
            >>> fig.show()
            >>> # Save plot to HTML
            >>> fig.write_html("indicator_plot.html")
            >>> # Customize further
            >>> fig.update_layout(height=600, width=1000)
            >>> fig.show()
        """
        from pyptine.visualization.charts import plot_indicator

        return plot_indicator(
            self._chronological(),
            title=kwargs.pop("title", self.title),
            x_column=x_column,
            y_column=y_column,
            chart_type=chart_type,
            color_column=color_column,
            **kwargs,
        )

    def plot_line(
        self,
        x_column: str = "period",
        y_column: str = "value",
        color_column: Optional[str] = None,
        markers: bool = True,
        **kwargs: Any,
    ) -> Any:
        """Create an interactive line chart of the indicator data.

        Args:
            x_column: Column name for x-axis (default: "period", INE's period label)
            y_column: Column name for y-axis values (default: "value")
            color_column: Optional column for coloring lines
            markers: Show markers on line points (default: True)
            **kwargs: Additional plotly arguments

        Returns:
            Plotly figure object

        Example:
            >>> response = ine.get_data("0004127")
            >>> fig = response.plot_line(markers=True)
            >>> fig.show()
        """
        from pyptine.visualization.charts import plot_line_chart

        return plot_line_chart(
            self._chronological(),
            title=kwargs.pop("title", self.title),
            x_column=x_column,
            y_column=y_column,
            color_column=color_column,
            markers=markers,
            **kwargs,
        )

    def plot_bar(
        self,
        x_column: str = "period",
        y_column: str = "value",
        color_column: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        """Create an interactive bar chart of the indicator data.

        Args:
            x_column: Column name for x-axis (categories)
            y_column: Column name for y-axis values
            color_column: Optional column for coloring bars
            **kwargs: Additional plotly arguments

        Returns:
            Plotly figure object

        Example:
            >>> response = ine.get_data("0004127")
            >>> fig = response.plot_bar()
            >>> fig.show()
        """
        from pyptine.visualization.charts import plot_bar_chart

        return plot_bar_chart(
            self._chronological(),
            title=kwargs.pop("title", self.title),
            x_column=x_column,
            y_column=y_column,
            color_column=color_column,
            **kwargs,
        )

    def plot_area(
        self,
        x_column: str = "period",
        y_column: str = "value",
        color_column: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        """Create an interactive area chart of the indicator data.

        Args:
            x_column: Column name for x-axis
            y_column: Column name for y-axis values
            color_column: Optional column for coloring areas
            **kwargs: Additional plotly arguments

        Returns:
            Plotly figure object

        Example:
            >>> response = ine.get_data("0004127")
            >>> fig = response.plot_area()
            >>> fig.show()
        """
        from pyptine.visualization.charts import plot_area_chart

        return plot_area_chart(
            self._chronological(),
            title=kwargs.pop("title", self.title),
            x_column=x_column,
            y_column=y_column,
            color_column=color_column,
            **kwargs,
        )

    def plot_scatter(
        self,
        x_column: str = "period",
        y_column: str = "value",
        color_column: Optional[str] = None,
        size_column: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        """Create an interactive scatter chart of the indicator data.

        Args:
            x_column: Column name for x-axis
            y_column: Column name for y-axis values
            color_column: Optional column for coloring points
            size_column: Optional column for point size
            **kwargs: Additional plotly arguments

        Returns:
            Plotly figure object

        Example:
            >>> response = ine.get_data("0004127")
            >>> fig = response.plot_scatter()
            >>> fig.show()
        """
        from pyptine.visualization.charts import plot_scatter_chart

        return plot_scatter_chart(
            self._chronological(),
            title=kwargs.pop("title", self.title),
            x_column=x_column,
            y_column=y_column,
            color_column=color_column,
            size_column=size_column,
            **kwargs,
        )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "varcd": "0004167",
                "title": "Resident population",
                "language": "EN",
                "data": [
                    {
                        "period": "2021",
                        "period_code": "S7A2021",
                        "geocod": "PT",
                        "value": 10599117.0,
                    },
                    {
                        "period": "2022",
                        "period_code": "S7A2022",
                        "geocod": "PT",
                        "value": 10929704.0,
                    },
                ],
                "unit": "No.",
            }
        }
    )


class CatalogueResponse(BaseModel):
    """Indicators from INE's catalogue, with provenance.

    ``extraction_date`` is INE's own ``<extraction_date>``, naive: INE states no timezone
    (see :func:`pyptine.provenance.parse_catalogue_date`).
    """

    indicators: list["Indicator"] = Field(default_factory=list, description="List of indicators")
    language: str = Field(..., description="Language (PT or EN)")
    extraction_date: datetime = Field(
        ..., description="INE's catalogue <extraction_date> (INE's clock, timezone not stated)"
    )
    url: Optional[str] = Field(None, description="The catalogue request's URL")
    total_count: int = Field(0, description="Total number of indicators")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def source(self) -> str:
        return INE_SOURCE

    @computed_field  # type: ignore[prop-decorator]
    @property
    def licence(self) -> str:
        return INE_LICENCE

    def __len__(self) -> int:
        """Return number of indicators."""
        return len(self.indicators)

    def __iter__(self) -> Iterator[Indicator]:  # type: ignore[override]
        """Iterate over indicators."""
        return iter(self.indicators)

    def __getitem__(self, index: int) -> Indicator:
        """Get indicator by index."""
        return self.indicators[index]


CatalogueResponse.model_rebuild()
