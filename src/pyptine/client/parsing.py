"""Parsing and validation shared by the sync and async INE clients.

Facts this module relies on, verified against the live API on 2026-11-27 and pinned in tests
against the recorded responses in tests/fixtures/ine/:

- Every JSON response is a one-element array around an object.
- INE refuses with HTTP 200 and ``{"Sucesso": {"Falso": [{"Msg", "Cod"}]}}``.
- ``Dados`` maps the period *label* ("2022", "1st Quarter 2026") to its rows. Rows carry no
  period field. ``Dim1`` takes the period *code* ("S7A2022"), the dimension's ``cat_id``.
- Dimension filters take ``cat_id``, not ``categ_cod`` (0012234 Dim3: "H1", not "H.1").
- Several codes go as one comma-separated value ("S7A2021,S7A2022"). Unfiltered, only the
  latest period is returned. ``start``/``count`` are ignored.
"""

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pyptine.geography import classify_geography
from pyptine.models.indicator import Dimension, IndicatorMetadata
from pyptine.models.response import DataResponse
from pyptine.provenance import parse_extraction_date
from pyptine.utils.exceptions import DataProcessingError, DimensionError, INERefusalError

logger = logging.getLogger(__name__)

#: A dimension filter value: one code, a comma-separated string of codes, or a list of codes.
DimensionValueFilter = str | Sequence[str]
DimensionFilters = Mapping[str, DimensionValueFilter]

# A refusal's key as INE writes it ('"Falso" : ['). A label "Falso" is never followed by a colon.
_REFUSAL = re.compile(rb'"Falso"\s*:')

# Markers INE uses in `valor` for suppressed or unavailable figures.
_SUPPRESSION_MARKERS = {"", "-", "x", "//", "...", "..", "§", "o"}


def is_refusal_body(content: bytes) -> bool:
    """True if a raw response body is an INE refusal (used to keep refusals out of the cache)."""
    return bool(_REFUSAL.search(content))


# The catalogue (xml_indic.jsp) answers a code it does not hold with HTTP 200 and a Java
# stack trace instead of XML: "<pre>java.lang.NullPointerException\n\tat netgest.bo...".
# Verified 2026-11-27 for 0004127 and 9999999 (opc=1, either language).
_JAVA_ERROR = re.compile(rb"^\s*<pre>\s*(java\.[\w.$]+(?:Exception|Error))")


def java_error(content: bytes) -> str | None:
    """The Java exception name if ``content`` is INE's error page, else None."""
    m = _JAVA_ERROR.match(content[:500])
    return m.group(1).decode("ascii", "replace") if m else None


def check_catalogue_body(content: bytes, varcd: str = "", url: str = "") -> None:
    """Raise INERefusalError if the catalogue answered with its Java error page.

    INE does this, deterministically, for a code that is not in its catalogue, so it is
    refused at once (not retried as a malformed body) and never cached.
    """
    exception = java_error(content)
    if exception is not None:
        raise INERefusalError(
            varcd=varcd,
            ine_code="",
            ine_message=(
                f"the catalogue answered with a server error page ({exception}) instead of "
                "XML, which is how it answers a code that is not in INE's catalogue"
            ),
            url=url,
        )


def check_success(body: Any, varcd: str = "", url: str = "") -> None:
    """Raise INERefusalError if INE refused the request.

    Args:
        body: Parsed JSON body (INE's one-element array, or the object inside it)
        varcd: Indicator code of the request, for the message
        url: Request URL, for the message

    Raises:
        INERefusalError: If the body carries ``Sucesso.Falso``
    """
    obj = body[0] if isinstance(body, list) and len(body) == 1 else body
    if not isinstance(obj, dict):
        return
    falso = (
        (obj.get("Sucesso") or {}).get("Falso") if isinstance(obj.get("Sucesso"), dict) else None
    )
    if falso:
        first = falso[0] if isinstance(falso, list) and falso else {}
        raise INERefusalError(
            varcd=str(first.get("IndicadorCod") or varcd),
            ine_code=str(first.get("Cod", "")),
            ine_message=str(first.get("Msg", "")),
            url=url,
        )


def split_codes(value: DimensionValueFilter) -> list[str]:
    """Split a filter value into codes: "a,b" or ["a", "b"] -> ["a", "b"]."""
    if isinstance(value, str):
        codes = value.split(",")
    elif isinstance(value, Sequence):
        codes = [str(v) for v in value]
    else:
        codes = [str(value)]
    return [c.strip() for c in codes]


def _dimension_map(metadata: IndicatorMetadata) -> dict[str, Dimension]:
    return {f"Dim{d.id}": d for d in metadata.dimensions}


def _invalid_value_message(varcd: str, key: str, code: str, dim: Dimension) -> str:
    msg = f"Invalid value '{code}' for dimension '{key}' of indicator {varcd}."
    by_categ = [v for v in dim.values if v.categ_cod == code and v.code != code]
    by_label = [v for v in dim.values if v.label == code]
    if len(by_categ) == 1:
        msg += (
            f" '{code}' is the categ_cod of '{by_categ[0].code}'; INE filters by cat_id, "
            f"so use '{by_categ[0].code}'."
        )
    elif len(by_label) == 1:
        msg += f" '{code}' is the label of '{by_label[0].code}': use the code '{by_label[0].code}'."
    valid = [v.code for v in dim.values]
    shown = ", ".join(valid[:12]) + (", ..." if len(valid) > 12 else "")
    return msg + f" Valid codes ({len(valid)}): {shown}"


def normalise_dimensions(
    varcd: str, dimensions: DimensionFilters, metadata: IndicatorMetadata
) -> dict[str, str]:
    """Validate dimension filters against the indicator's metadata.

    Each value may be one code, a comma-separated string or a list of codes. Codes are the
    dimension's ``cat_id`` values (``DimensionValue.code``).

    Returns:
        The filters as INE takes them: one comma-separated string per dimension

    Raises:
        DimensionError: For an unknown dimension key, an empty value, or a code that is not
            valid for the dimension (the message names the code to use when the given one is a
            label or a ``categ_cod``)
    """
    available = _dimension_map(metadata)
    out: dict[str, str] = {}
    for key, value in dimensions.items():
        if key not in available:
            raise DimensionError(
                f"Invalid dimension key '{key}' for indicator {varcd}. "
                f"Available keys: {list(available.keys())}"
            )
        codes = split_codes(value)
        if not codes or any(c == "" for c in codes):
            raise DimensionError(
                f"Empty code in dimension '{key}' for indicator {varcd}: {value!r}"
            )
        dim = available[key]
        valid = {v.code for v in dim.values}
        for code in codes:
            if code not in valid:
                raise DimensionError(_invalid_value_message(varcd, key, code, dim))
        out[key] = ",".join(codes)
    return out


def period_codes(metadata: IndicatorMetadata) -> list[str]:
    """Every period code (Dim1 cat_id) of an indicator, oldest first."""
    dim = _dimension_map(metadata).get("Dim1")
    if dim is None or not dim.values:
        raise DataProcessingError(f"Metadata for {metadata.varcd} lists no periods (Dim1)")
    return [v.code for v in sorted(dim.values, key=lambda v: (v.order or 0, v.code))]


def _period_lookup(metadata: IndicatorMetadata) -> dict[str, str]:
    """Period label -> period code, from Dim1 of the metadata."""
    dim = _dimension_map(metadata).get("Dim1")
    if dim is None:
        raise DataProcessingError(f"Metadata for {metadata.varcd} has no Dim1 (period)")
    lookup: dict[str, str] = {}
    for v in dim.values:
        if v.label in lookup and lookup[v.label] != v.code:
            raise DataProcessingError(
                f"Period label {v.label!r} of {metadata.varcd} names two codes "
                f"({lookup[v.label]}, {v.code}); cannot assign period codes"
            )
        lookup[v.label] = v.code
    return lookup


def process_data_point(data_point: dict[str, Any]) -> dict[str, Any]:
    """Convert one INE data row: ``valor`` becomes the float ``value``; other fields stay.

    ``valor`` is the machine form (decimal point); ``ind_string`` is the localised display form
    and is kept as is. Suppression markers and nulls become None.

    Raises:
        DataProcessingError: If the row is not an object
    """
    if not isinstance(data_point, dict):
        raise DataProcessingError(f"Data row is not an object: {data_point!r}")
    processed: dict[str, Any] = {}
    for key, value in data_point.items():
        if key.startswith("_"):
            continue
        if key in ("valor", "value"):
            processed["value"] = _to_float(value)
        else:
            processed[key] = value
    return processed


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text in _SUPPRESSION_MARKERS:
        return None
    try:
        return float(text)
    except ValueError:
        logger.warning(f"Unexpected value {value!r} in INE data; recorded as missing")
        return None


def parse_data_response(
    varcd: str,
    response: dict[str, Any] | list[Any],
    language: str,
    get_metadata: Callable[[], IndicatorMetadata | None],
    url: str | None = None,
    dimensions: Mapping[str, str] | None = None,
) -> DataResponse:
    """Parse a data response into a DataResponse.

    Each row of INE's ``Dados`` gets ``period`` (INE's label, in the request language) and
    ``period_code`` (the Dim1 code, e.g. "S7A2022"), taken from the indicator's metadata.
    Provenance comes from INE's response (``DataExtracao``, ``DataUltimoAtualizacao``,
    ``MetaInfUrl``) and from the request (``url``, ``dimensions``).

    Args:
        varcd: Indicator code requested
        response: Raw JSON body
        language: Request language
        get_metadata: Returns the indicator's metadata in the same language (called lazily)
        url: The request's URL, recorded for provenance
        dimensions: The dimension filters sent (normalised), recorded for provenance

    Raises:
        INERefusalError: If INE refused the request
        DataProcessingError: If the response has an unknown shape, a period label that the
            metadata does not list, or no ``DataExtracao``
    """
    check_success(response, varcd)
    rows: list[dict[str, Any]] = []
    metadata: IndicatorMetadata | None = None
    extracted = None
    last_updated: str | None = None
    metadata_url: str | None = None
    geo_classification: str | None = None

    def meta() -> IndicatorMetadata | None:
        nonlocal metadata
        if metadata is None:
            metadata = get_metadata()
        return metadata

    try:
        if isinstance(response, list):
            if len(response) != 1 or not isinstance(response[0], dict):
                raise DataProcessingError(
                    f"Unexpected data response for {varcd}: a list of {len(response)} "
                    "items (INE sends one object inside a list)"
                )
            response = response[0]
        if not isinstance(response, dict):
            raise DataProcessingError(f"Unexpected data response for {varcd}: {type(response)}")

        dados = response.get("Dados")
        if not isinstance(dados, dict):
            raise DataProcessingError(
                f"INE's data response for {varcd} has no Dados object "
                f"(got {type(dados).__name__}; keys: {sorted(response)})"
            )
        varcd_val = response.get("IndicadorCod") or varcd
        title = response.get("IndicadorDsg") or ""
        lang = language  # the data response has no Lingua: the request language
        # Provenance is required, so a missing timestamp is refused.
        extracted = parse_extraction_date(response.get("DataExtracao"), varcd)
        last_updated = response.get("DataUltimoAtualizacao")
        metadata_url = response.get("MetaInfUrl")
        md = meta()
        if md is None:
            raise DataProcessingError(
                f"Period codes for {varcd} need its metadata, which is not available"
            )
        lookup = _period_lookup(md)
        geo = [d for d in md.dimensions if d.id == 2]
        if geo:  # Dim2 is geography; its full code list tells NUTS from districts
            geo_classification = classify_geography(v.code for v in geo[0].values)
        for label, period_rows in dados.items():
            code = lookup.get(label)
            if code is None:
                raise DataProcessingError(
                    f"INE keyed data for {varcd} by period {label!r}, which the "
                    f"indicator's metadata ({md.language}) does not list; cannot "
                    "assign its period code"
                )
            for row in period_rows:
                rows.append({"period": label, "period_code": code, **process_data_point(row)})
    except (DataProcessingError, INERefusalError):
        raise
    except Exception as e:
        raise DataProcessingError(f"Failed to parse data for {varcd}: {e}") from e

    # The data response carries no unit; the metadata (already fetched) does.
    unit = md.unit
    title = title or md.title

    return DataResponse(
        varcd=varcd_val,
        title=title,
        language=lang,
        data=rows,
        unit=unit,
        extraction_date=extracted,
        last_updated=last_updated,
        metadata_url=metadata_url,
        geo_classification=geo_classification,
        url=url,
        dimensions=dict(dimensions or {}),
    )
