"""Pydantic models for INE indicators and dimensions."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DimensionValue(BaseModel):
    """Single dimension value/category.

    Represents a specific value that can be used to filter data
    along a particular dimension (e.g., a specific year, region, or category).
    """

    code: str = Field(..., description="Code to filter by (INE's cat_id, e.g. 'S7A2022' or 'H1')")
    label: str = Field(..., description="Human-readable label (in the request language)")
    order: Optional[int] = Field(default=None, description="Display order")
    categ_cod: Optional[str] = Field(
        default=None,
        description="INE's categ_cod. Usually equal to code; where it differs (e.g. 'H.1' for "
        "'H1'), INE refuses it as a filter.",
    )


class Dimension(BaseModel):
    """Dimension definition for an indicator.

    Dimensions allow filtering and organizing statistical data
    (e.g., time period, geographic region, demographic category).
    """

    id: int = Field(..., description="Dimension identifier")
    name: str = Field(..., description="Dimension name")
    description: Optional[str] = Field(default=None, description="Dimension description")
    values: list[DimensionValue] = Field(default_factory=list, description="Available values")


class Indicator(BaseModel):
    """Indicator metadata from INE catalogue.

    Represents a statistical indicator with its metadata including
    title, description, theme classification, and update information.
    """

    varcd: str = Field(..., description="Indicator code (e.g., '0004167')")
    title: str = Field(..., description="Indicator title")
    description: Optional[str] = Field(default=None, description="Detailed description")
    theme: Optional[str] = Field(default=None, description="Primary theme")
    subtheme: Optional[str] = Field(default=None, description="Sub-theme")
    keywords: list[str] = Field(default_factory=list, description="Search keywords")
    periodicity: Optional[str] = Field(default=None, description="Update frequency")
    last_period: Optional[str] = Field(default=None, description="Last data period")
    last_update: Optional[datetime] = Field(default=None, description="Last update timestamp")
    geo_last_level: Optional[str] = Field(default=None, description="Geographic detail level")
    unit: Optional[str] = Field(
        default=None, description="Unit of measurement (metadata only; the catalogue sends none)"
    )

    # API URLs
    html_url: Optional[str] = Field(default=None, description="Web page URL")
    metadata_url: Optional[str] = Field(default=None, description="Metadata API URL")
    data_url: Optional[str] = Field(default=None, description="Data API URL")
    source: Optional[str] = Field(default=None, description="Data source")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "varcd": "0004167",
                "title": "Resident population",
                "description": "Annual resident population estimates",
                "theme": "Population",
                "keywords": ["population", "residents", "demographics"],
                "periodicity": "Annual",
                "last_period": "2023",
            }
        }
    )


class IndicatorMetadata(Indicator):
    """Complete metadata for an indicator including dimensions.

    From INE's metadata endpoint (``pindicaMeta``): title, unit, periodicity, last period,
    last update and dimensions. That endpoint sends no description, theme, subtheme,
    keywords, source, geographic level or URLs: they are None unless filled from the
    catalogue (``INE.get_metadata`` does so by default; see
    :func:`pyptine.client.metadata.with_catalogue`).
    """

    language: str = Field(..., description="Language (PT or EN)")
    dimensions: list[Dimension] = Field(default_factory=list, description="Available dimensions")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "varcd": "0004167",
                "title": "Resident population",
                "language": "EN",
                "dimensions": [
                    {
                        "id": 1,
                        "name": "Period",
                        "values": [
                            {"code": "2020", "label": "2020"},
                            {"code": "2021", "label": "2021"},
                        ],
                    }
                ],
            }
        }
    )
