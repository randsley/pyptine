"""Provenance and the citation INE's reuse terms require.

Data from Statistics Portugal (INE) is open for reuse, commercial use included, under EU
Directive 2019/1024; INE retains copyright and the single condition is that the source be
cited. :func:`ine_citation` builds that sentence with the indicator codes, the reference
periods and INE's extraction date filled in (as PortugalINE.jl's ``ine_citation``).
"""

import re
import urllib.parse
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from pyptine.utils.exceptions import DataProcessingError

if TYPE_CHECKING:
    from pyptine.models.response import DataResponse

#: The attribution INE's reuse terms require.
INE_ATTRIBUTION = "Source: Statistics Portugal (INE)."

#: INE's reuse terms, in one line.
INE_LICENCE = (
    "Statistics Portugal (INE) data: reuse permitted, commercial use included, under EU "
    "Directive 2019/1024, on condition that the source is cited. INE retains copyright."
)

#: Who published the data.
INE_SOURCE = "Statistics Portugal (INE), https://www.ine.pt"


def request_url(base: str, endpoint: str, params: Mapping[str, Any]) -> str:
    """The URL of a request, readable (commas unescaped; INE accepts them as sent)."""
    return f"{base}{endpoint}?{urllib.parse.urlencode(dict(params), safe=',')}"


def parse_extraction_date(value: Any, varcd: str) -> datetime:
    """INE's ``DataExtracao`` (``2026-11-27T16:17:41.734Z``) as an aware UTC datetime.

    Raises:
        DataProcessingError: If the value is missing or not an ISO timestamp
    """
    if not isinstance(value, str) or not value:
        raise DataProcessingError(
            f"INE's data response for {varcd} has no DataExtracao (its extraction timestamp), "
            "which provenance needs"
        )
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as e:
        raise DataProcessingError(
            f"INE's DataExtracao for {varcd} is not an ISO timestamp: {value!r}"
        ) from e
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# The catalogue's <extraction_date>, e.g. "Friday, 27 November 2026, 04:17:58.067 PM". The
# same English form under lang=EN and lang=PT, for opc=1, 2 and 3 (checked 2026-11-27).
_CATALOGUE_DATE = re.compile(
    r"^(?P<wd>[A-Z][a-z]+), (?P<d>\d{1,2}) (?P<mon>[A-Z][a-z]+) (?P<y>\d{4}), "
    r"(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})(?:\.(?P<ms>\d{1,6}))? (?P<ampm>AM|PM)$"
)
_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
    )
}
_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def parse_catalogue_date(value: Any) -> datetime:
    """The catalogue's ``<extraction_date>`` as a naive datetime on INE's clock.

    INE states no timezone. On 2026-11-27 the catalogue's time matched the data endpoint's
    UTC ``DataExtracao``, but Lisbon is UTC+0 in winter, so whether it is UTC or Lisbon time
    is not known; the value is left naive rather than guessed. Month and weekday names are
    read from a fixed table (not the locale), and the weekday must agree with the date.

    Raises:
        DataProcessingError: If the value is missing or not in INE's form
    """
    text = value.strip() if isinstance(value, str) else ""
    m = _CATALOGUE_DATE.match(text)
    if not m or m["mon"] not in _MONTHS or m["wd"] not in _WEEKDAYS:
        raise DataProcessingError(
            f"INE's catalogue <extraction_date> {value!r} is not in the form "
            "'Friday, 27 November 2026, 04:17:58.067 PM'"
        )
    hour = int(m["h"])
    if not 1 <= hour <= 12:
        raise DataProcessingError(f"INE's catalogue <extraction_date> {value!r}: bad hour")
    hour = hour % 12 + (12 if m["ampm"] == "PM" else 0)
    micro = int((m["ms"] or "0").ljust(6, "0"))
    try:
        parsed = datetime(
            int(m["y"]), _MONTHS[m["mon"]], int(m["d"]), hour, int(m["mi"]), int(m["s"]), micro
        )
    except ValueError as e:
        raise DataProcessingError(f"INE's catalogue <extraction_date> {value!r}: {e}") from e
    if _WEEKDAYS[parsed.weekday()] != m["wd"]:
        raise DataProcessingError(
            f"INE's catalogue <extraction_date> {value!r}: {m['wd']} does not match the date"
        )
    return parsed


# Period codes whose position is known, verified 2026-11-27: annual S7A2022, quarterly
# S5A20261, monthly S3A202601. (kind, position) lets a run of consecutive periods be written as
# a range; a code of any other form is listed as it is.
_PERIOD_FORMS = (
    (re.compile(r"S7A(\d{4})"), "annual", lambda m: int(m[1])),
    (re.compile(r"S5A(\d{4})([1-4])"), "quarterly", lambda m: int(m[1]) * 4 + int(m[2])),
    (re.compile(r"S3A(\d{4})(0[1-9]|1[0-2])"), "monthly", lambda m: int(m[1]) * 12 + int(m[2])),
)


def _period_position(code: str) -> tuple[str, int] | None:
    for pattern, kind, position in _PERIOD_FORMS:
        m = pattern.fullmatch(code)
        if m:
            return kind, position(m)
    return None


def period_ranges(periods: Mapping[str, str]) -> list[str]:
    """INE's period labels, in code order, with runs of three or more consecutive periods
    written as "first to last" (only for code forms whose position is known)."""
    codes = sorted(periods)
    out: list[str] = []
    i = 0
    while i < len(codes):
        j = i
        here = _period_position(codes[i])
        while here is not None and j + 1 < len(codes):
            following = _period_position(codes[j + 1])
            if following is None or following[0] != here[0] or following[1] != here[1] + 1:
                break
            j, here = j + 1, following
        label = [periods[c] or c for c in (codes[i], codes[j])]
        if j - i >= 2:
            out.append(f"{label[0]} to {label[1]}")
        else:
            out.extend(periods[c] or c for c in codes[i : j + 1])
        i = j + 1
    return out


def ine_citation(*responses: "DataResponse") -> str:
    """The citation INE's reuse terms require, for every extract that went into a result.

    Example:
        >>> ine_citation(response)
        'Source: Statistics Portugal (INE), indicator 0008273, reference periods 2021, 2022,
        extracted 2026-11-27.'

    Raises:
        ValueError: If nothing is given, or an extract has no INE extraction date
    """
    if not responses:
        raise ValueError("nothing to cite")
    missing = sorted({r.varcd for r in responses if r.extraction_date is None})
    if missing:
        raise ValueError(
            f"no INE extraction date for {', '.join(missing)} (not fetched from INE's data "
            "endpoint); cannot cite"
        )
    codes = sorted({r.varcd for r in responses})
    periods: dict[str, str] = {}
    for r in responses:
        for row in r.data:
            if "period_code" in row:
                periods.setdefault(str(row["period_code"]), str(row.get("period", "")))
    labels = period_ranges(periods)
    date: datetime | None = min(r.extraction_date for r in responses if r.extraction_date)
    assert date is not None
    text = "Source: Statistics Portugal (INE), indicator"
    text += (" " if len(codes) == 1 else "s ") + ", ".join(codes)
    if labels:
        several = len(periods) > 1
        text += ", reference period" + ("s " if several else " ") + ", ".join(labels)
    return f"{text}, extracted {date.date().isoformat()}."
