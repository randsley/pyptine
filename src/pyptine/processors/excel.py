"""Excel export functionality for pyptine."""

import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

import pandas as pd

from pyptine.utils.exceptions import DataProcessingError

if TYPE_CHECKING:
    from pyptine.models.response import DataResponse

logger = logging.getLogger(__name__)


#: Name of the sheet holding the provenance.
PROVENANCE_SHEET = "Provenance"

_SHEET_FORBIDDEN = set("[]:*?/\\")


def _sheet_name(name: Any) -> str:
    """A valid Excel sheet name: at most 31 characters, none of ``[]:*?/\\``."""
    text = "".join("_" if c in _SHEET_FORBIDDEN else c for c in str(name))[:31]
    if not text:
        raise DataProcessingError(f"Empty Excel sheet name from {name!r}")
    return text


def export_multiple_sheets(
    data_dict: Mapping[str, Union[pd.DataFrame, "DataResponse"]],
    filepath: str | Path,
    include_metadata: bool = True,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Export data to an Excel file, one sheet each, with a provenance sheet.

    Values may be ``DataResponse`` objects (from INE's data endpoint) or DataFrames. With
    ``include_metadata`` (the default), a first sheet named ``Provenance`` lists, for each
    ``DataResponse``, its source, licence, citation, indicator, periods, dimension filters,
    request URL and INE's extraction date (``DataResponse.provenance()``), one column per
    data sheet, plus when the file was written. ``metadata`` adds rows of your own (e.g.
    for DataFrames, which carry no provenance).

    Args:
        data_dict: ``{sheet_name: DataResponse or DataFrame}``
        filepath: Output Excel file path
        include_metadata: Write the Provenance sheet
        metadata: Extra provenance rows, ``{field: value}``, applied to every sheet

    Raises:
        DataProcessingError: If openpyxl is missing, sheet names clash (after Excel's
            31-character limit), a sheet is named ``Provenance``, or the export fails

    Example:
        >>> export_multiple_sheets({"population": response}, Path("output.xlsx"))
    """
    from pyptine.models.response import DataResponse

    try:
        import openpyxl  # noqa: F401
    except ImportError:
        raise DataProcessingError(
            "openpyxl is required for Excel export. Install with: pip install pyptine[excel]"
        ) from None

    names = {key: _sheet_name(key) for key in data_dict}
    clashes = sorted({n for n in names.values() if list(names.values()).count(n) > 1})
    if clashes:
        raise DataProcessingError(f"Excel sheet names clash after truncation: {clashes}")
    if include_metadata and PROVENANCE_SHEET in names.values():
        raise DataProcessingError(f"Sheet name {PROVENANCE_SHEET!r} is reserved")

    provenance: dict[str, dict[str, Any]] = {}
    for key, value in data_dict.items():
        info = value.provenance() if isinstance(value, DataResponse) else {}
        if metadata:
            info = {**info, **{k: v for k, v in metadata.items() if k not in info}}
        provenance[names[key]] = info

    try:
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
            if include_metadata:
                fields: list[str] = []
                for info in provenance.values():
                    fields += [f for f in info if f not in fields]
                written = datetime.now(timezone.utc).isoformat(timespec="seconds")
                table = pd.DataFrame(
                    {sheet: [info.get(f) for f in fields] for sheet, info in provenance.items()},
                    index=pd.Index(fields, name="field"),
                )
                table.loc["written"] = written
                table.reset_index().to_excel(writer, sheet_name=PROVENANCE_SHEET, index=False)
            for key, value in data_dict.items():
                df = value.to_dataframe() if isinstance(value, DataResponse) else value
                df.to_excel(writer, sheet_name=names[key], index=False)
        logger.info(f"Exported {len(data_dict)} sheets to {filepath}")
    except Exception as e:
        logger.error(f"Failed to export Excel: {str(e)}")
        raise DataProcessingError(f"Failed to export Excel: {str(e)}") from e


def format_for_excel(
    df: pd.DataFrame,
    date_format: str = "%Y-%m-%d",
    float_format: str = "%.2f",
) -> pd.DataFrame:
    """Format DataFrame for Excel export.

    Args:
        df: Input DataFrame
        date_format: Format string for dates
        float_format: Format string for floats

    Returns:
        Formatted DataFrame

    Example:
        >>> df = pd.DataFrame({"value": [1.2345, 2.3456]})
        >>> formatted = format_for_excel(df)
    """
    df = df.copy()

    # Format datetime columns
    for col in df.select_dtypes(include=["datetime64"]).columns:  # type: str
        df[col] = df[col].dt.strftime(date_format)

    # Format float columns
    for float_col in df.select_dtypes(include=["float64"]).columns:  # type: str
        df[float_col] = df[float_col].apply(lambda x: float(float_format % x) if pd.notna(x) else x)

    return df
