"""Statistical metrics for data analysis.

Every function works on one series: one row per period. INE data usually has several rows per
period (every geographic unit, every category of an extra dimension), so filter to one series
first, e.g. ``[r for r in response.data if r["geocod"] == "PT"]``, or request one with
dimension filters. The functions refuse data with several rows per period.

Periods are sorted by ``period_column``. The default is ``period_code`` (INE's period code,
e.g. "S7A2022"), which sorts chronologically within an indicator. Labels such as
"1st Quarter 2026" do not, so do not sort by ``period``.
"""

import logging
import re
from typing import Any, Optional, cast

try:
    import pandas as pd

    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

logger = logging.getLogger(__name__)

DEFAULT_PERIOD_COLUMN = "period_code"

# INE period codes: S<n>A then the year, then the quarter (1 digit) or month (2 digits).
# Annual S7A2022, quarterly S5A20261, monthly S3A202601 (verified 2026-11-27).
_INE_PERIOD = re.compile(r"^(S\d+A)(\d{4})(\d{0,2})$")
# Plain forms: 2022, 2022-01.
_PLAIN_PERIOD = re.compile(r"^(\d{4})(-\d{2})?$")


def _require_pandas(what: str) -> None:
    if not PANDAS_AVAILABLE:
        raise ImportError(f"pandas is required for {what}. Install with: pip install pandas")


def _series_frame(
    data: list[dict[str, Any]], value_column: str, period_column: str
) -> "pd.DataFrame":
    """One series as a DataFrame sorted by period; refuses missing columns and repeats."""
    if any(value_column not in row or period_column not in row for row in data):
        raise ValueError(f"Data must contain '{value_column}' and '{period_column}' columns")
    df = pd.DataFrame(data).copy()
    repeats = df[period_column].duplicated(keep=False)
    if repeats.any():
        n_periods = df[period_column].nunique()
        raise ValueError(
            f"Data has several rows per period ({len(df)} rows, {n_periods} periods in "
            f"'{period_column}'): it holds more than one series (e.g. several geographic units "
            "or categories). Filter to one series first, e.g. one geocod and one code of each "
            "extra dimension."
        )
    df[value_column] = pd.to_numeric(df[value_column], errors="coerce")
    return df.sort_values(by=period_column, kind="stable")


def _year_earlier(period: Any) -> Optional[str]:
    """The same period one year earlier, or None if the form is not recognised."""
    text = str(period)
    m = _INE_PERIOD.match(text)
    if m:
        return f"{m.group(1)}{int(m.group(2)) - 1}{m.group(3)}"
    m = _PLAIN_PERIOD.match(text)
    if m:
        return f"{int(m.group(1)) - 1}{m.group(2) or ''}"
    return None


def _records(df: "pd.DataFrame") -> list[dict[str, Any]]:
    df = df.astype(object).where(df.notna(), None)
    return cast(list[dict[str, Any]], df.to_dict(orient="records"))


def calculate_yoy_growth(
    data: list[dict[str, Any]],
    value_column: str = "value",
    period_column: str = DEFAULT_PERIOD_COLUMN,
) -> list[dict[str, Any]]:
    """Calculate year-over-year growth rates (%).

    Compares each period with the same period one year earlier (2022 with 2021, 1st quarter
    2026 with 1st quarter 2025, January 2026 with January 2025), not with the previous row.
    ``yoy_growth`` is None where that period is absent or its value is missing or zero.

    Args:
        data: One series (one row per period)
        value_column: Column with the values (default: "value")
        period_column: Column with the periods (default: "period_code"). Accepted forms: INE
            period codes (S7A2022, S5A20261, S3A202601), years (2022) and year-months (2022-01).

    Returns:
        The rows sorted by period, with an added 'yoy_growth' column

    Raises:
        ValueError: If columns are missing, a period has several rows, or a period is in a
            form whose year-earlier period cannot be determined

    Example:
        >>> data = [{"period_code": "S7A2021", "value": 100}, {"period_code": "S7A2022", "value": 110}]
        >>> [r["yoy_growth"] for r in calculate_yoy_growth(data)]
        [None, 10.0]
    """
    _require_pandas("YoY growth calculation")
    if not data:
        return []
    df = _series_frame(data, value_column, period_column)
    by_period = dict(zip(df[period_column].astype(str), df[value_column]))
    growth: list[Optional[float]] = []
    for period, value in zip(df[period_column], df[value_column]):
        earlier = _year_earlier(period)
        if earlier is None:
            raise ValueError(
                f"Cannot tell the period a year before {period!r}; use INE period codes "
                "(the 'period_code' column), years (2022) or year-months (2022-01)"
            )
        previous = by_period.get(earlier)
        if previous is None or pd.isna(previous) or previous == 0 or pd.isna(value):
            growth.append(None)
        else:
            growth.append((value / previous - 1) * 100)
    df["yoy_growth"] = growth
    logger.debug(f"Calculated YoY growth for {len(df)} data points")
    return _records(df)


def calculate_mom_change(
    data: list[dict[str, Any]],
    value_column: str = "value",
    period_column: str = DEFAULT_PERIOD_COLUMN,
) -> list[dict[str, Any]]:
    """Calculate the percentage change from the preceding period.

    Month-over-month for monthly data; for other frequencies it is the change from the
    preceding period present in the data (quarter-over-quarter, and so on).

    Args:
        data: One series (one row per period)
        value_column: Column with the values (default: "value")
        period_column: Column with the periods (default: "period_code")

    Returns:
        The rows sorted by period, with an added 'mom_change' column (None for the first)

    Raises:
        ValueError: If columns are missing or a period has several rows
    """
    _require_pandas("MoM change calculation")
    if not data:
        return []
    df = _series_frame(data, value_column, period_column)
    df["mom_change"] = df[value_column].pct_change(fill_method=None) * 100
    logger.debug(f"Calculated MoM change for {len(df)} data points")
    return _records(df)


def calculate_moving_average(
    data: list[dict[str, Any]],
    window: int = 3,
    value_column: str = "value",
    period_column: str = DEFAULT_PERIOD_COLUMN,
) -> list[dict[str, Any]]:
    """Calculate a simple moving average over ``window`` consecutive periods.

    Args:
        data: One series (one row per period)
        window: Number of periods in the average (default: 3)
        value_column: Column with the values (default: "value")
        period_column: Column with the periods (default: "period_code")

    Returns:
        The rows sorted by period, with an added 'moving_avg' column (None for the first
        window-1 periods)

    Raises:
        ValueError: If columns are missing, a period has several rows, or window < 1
    """
    _require_pandas("moving average calculation")
    if not data:
        return []
    if window < 1:
        raise ValueError(f"Window size must be at least 1, got {window}")
    df = _series_frame(data, value_column, period_column)
    df["moving_avg"] = df[value_column].rolling(window=window, center=False).mean()
    logger.debug(f"Calculated {window}-period moving average for {len(df)} data points")
    return _records(df)


def calculate_exponential_moving_average(
    data: list[dict[str, Any]],
    span: int = 3,
    value_column: str = "value",
    period_column: str = DEFAULT_PERIOD_COLUMN,
) -> list[dict[str, Any]]:
    """Calculate an exponential moving average.

    Args:
        data: One series (one row per period)
        span: Span of the exponential weighting (default: 3)
        value_column: Column with the values (default: "value")
        period_column: Column with the periods (default: "period_code")

    Returns:
        The rows sorted by period, with an added 'ema' column

    Raises:
        ValueError: If columns are missing, a period has several rows, or span < 1
    """
    _require_pandas("EMA calculation")
    if not data:
        return []
    if span < 1:
        raise ValueError(f"Span must be at least 1, got {span}")
    df = _series_frame(data, value_column, period_column)
    df["ema"] = df[value_column].ewm(span=span, adjust=False).mean()
    logger.debug(f"Calculated EMA (span={span}) for {len(df)} data points")
    return _records(df)
