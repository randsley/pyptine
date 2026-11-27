"""Geographic levels of INE's geography codes (``geocod``), from the codes alone.

One data request returns every geographic level at once (country, NUTS I/II/III,
municipalities, parishes), and some indicators add rows that are not places. The level comes
from the code, never from the name: names are translated inconsistently (under ``lang=EN``
the mainland stays "Continente" and residence abroad is "Foreigner"). The rules are
PortugalINE.jl's ``Codes`` module (its "Geography traps"), verified on INE's data:

- Length 1 is NUTS I, 2 is NUTS II (``PT`` is the country), 3 is NUTS III, 7 is a municipality
  (NUTS III + DICO), 9 a parish (NUTS III + DICOFRE). Census 2021 indicators use **bare**
  codes: 4 characters is a DICO (municipality), 6 a DICOFRE (parish).
- 14 of 26 NUTS III codes contain a letter (``11A``, ``1A0``, ...), and the eight 2013 parish
  unions of Barcelos have letters in their DICOFRE (``0302FA``-``0302FH``).
- Pseudo-units, at every level: residence **abroad** (``YY``, ``0``, ``00``, ``000``,
  ``0000000``, ``000000000``), **unknown** residence (``9`` ... ``999999999``) and the grand
  **total** (``T``), as in live births 0008234. Their lengths are those of real levels, so
  they are recognised first. A municipality or parish code whose district is ``00`` or
  ``99`` is also one.

Any other code is refused (``DataProcessingError``) rather than assigned the nearest level.

**Two classifications.** Most indicators use NUTS for their geography (Dim2), but some use
districts (0004167, "Distrito/ Região": ``PT``, ``1``, the districts ``01``-``18``, ``2``,
``3``). There a two-digit code is a **district**, while under NUTS it is a NUTS II region
(``11`` is Lisboa's district in one, Norte in the other). The codes of a single row cannot
tell them apart, so the classification is taken from the dimension's full code list
(:func:`classify_geography`): a district dimension always holds ``01``-``09``, and no NUTS
II code starts with ``0`` (checked 2026-11-27).
"""

from collections.abc import Iterable

from pyptine.utils.exceptions import DataProcessingError

COUNTRY = "country"
NUTS1 = "nuts1"
NUTS2 = "nuts2"
NUTS3 = "nuts3"
DISTRICT = "district"
MUNICIPALITY = "municipality"
PARISH = "parish"

#: The levels of real places, largest first (district only under the district scheme).
LEVELS = (COUNTRY, NUTS1, NUTS2, NUTS3, DISTRICT, MUNICIPALITY, PARISH)

#: Classifications of a geography dimension.
NUTS_SCHEME = "nuts"
DISTRICT_SCHEME = "district"
CLASSIFICATIONS = (NUTS_SCHEME, DISTRICT_SCHEME)


def classify_geography(codes: Iterable[str]) -> str:
    """``"district"`` if a geography dimension's codes include a district (``01``-``09``),
    else ``"nuts"``. Pass every code of the dimension (from its metadata), not the rows of
    one response, which a filter may have narrowed."""
    for code in codes:
        if len(code) == 2 and code.isdigit() and code[0] == "0":
            return DISTRICT_SCHEME
    return NUTS_SCHEME


ABROAD = "abroad"
UNKNOWN = "unknown"
TOTAL = "total"

#: The kinds of pseudo-unit: rows INE adds that are not places.
PSEUDO_UNITS = (ABROAD, UNKNOWN, TOTAL)


def pseudo_unit(code: str) -> str | None:
    """``"abroad"``, ``"unknown"`` or ``"total"`` if ``code`` is not a place, else None."""
    if code == "T":
        return TOTAL
    if code == "YY" or (code and set(code) == {"0"}):
        return ABROAD
    if code and set(code) == {"9"}:
        return UNKNOWN
    district = _district(code)
    if district == "00":
        return ABROAD
    if district == "99":
        return UNKNOWN
    return None


def _district(code: str) -> str | None:
    """The district of a municipality or parish code (first two characters of the DICO)."""
    n = len(code)
    if n in (4, 7):  # DICO is the last 4
        return code[-4:-2]
    if n in (6, 9):  # DICOFRE is the last 6
        return code[-6:-4]
    return None


def _is_dicofre(code: str) -> bool:
    # Four digits, then two digits or capital letters (Barcelos's unions: 0302FA-0302FH).
    return (
        len(code) == 6
        and code[:4].isdigit()
        and all(c.isdigit() or "A" <= c <= "Z" for c in code[4:])
    )


def geo_level(code: str, classification: str = NUTS_SCHEME) -> str:
    """The level of a place's code: one of :data:`LEVELS`.

    Args:
        code: INE's ``geocod``
        classification: ``"nuts"`` (default) or ``"district"`` (see :func:`classify_geography`)

    Raises:
        DataProcessingError: If ``code`` is a pseudo-unit (see :func:`pseudo_unit`) or not a
            code INE issues, or the classification is unknown
    """
    if classification not in CLASSIFICATIONS:
        raise DataProcessingError(
            f"Unknown geography classification {classification!r}; use {CLASSIFICATIONS}"
        )
    kind = pseudo_unit(code)
    if kind is not None:
        raise DataProcessingError(f"{code!r} is not a place: INE uses it for {kind}")
    n = len(code)
    if n == 1:
        return NUTS1
    if n == 2:
        if code == "PT":
            return COUNTRY
        if classification == DISTRICT_SCHEME:
            if code.isdigit() and 1 <= int(code) <= 18:
                return DISTRICT
            raise DataProcessingError(
                f"Not a district code: {code!r} (the mainland districts are 01-18)"
            )
        return NUTS2
    if n == 3:
        return NUTS3
    if n == 7:
        return MUNICIPALITY
    if n == 9:
        return PARISH
    if n == 4:
        if code.isdigit():
            return MUNICIPALITY
        raise DataProcessingError(
            f"Not a geography code INE issues: {code!r} has length 4, and a 4-character code "
            "is a bare DICO, which is numeric"
        )
    if n == 6:
        if _is_dicofre(code):
            return PARISH
        raise DataProcessingError(
            f"Not a geography code INE issues: {code!r} has length 6, and a 6-character code "
            "is a bare DICOFRE: four digits, then two digits or capital letters"
        )
    raise DataProcessingError(
        f"Not a geography code INE issues: {code!r} has length {n}, and INE uses "
        "1, 2, 3, 4, 6, 7 and 9"
    )


def level_or_pseudo(code: str, classification: str = NUTS_SCHEME) -> str:
    """:func:`geo_level`, or the pseudo-unit's kind (``abroad``, ``unknown``, ``total``)."""
    return pseudo_unit(code) or geo_level(code, classification)


def check_codes(codes: Iterable[str], classification: str = NUTS_SCHEME) -> None:
    """Refuse every code that is neither a place nor a pseudo-unit, naming them all."""
    bad = []
    for code in dict.fromkeys(codes):
        try:
            level_or_pseudo(code, classification)
        except DataProcessingError:
            bad.append(code)
    if bad:
        shown = ", ".join(repr(c) for c in bad[:10]) + (" ..." if len(bad) > 10 else "")
        raise DataProcessingError(
            f"{len(bad)} geography code(s) INE is not known to issue: {shown}"
        )
