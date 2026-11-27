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
"""

from collections.abc import Iterable
from typing import Optional

from pyptine.utils.exceptions import DataProcessingError

COUNTRY = "country"
NUTS1 = "nuts1"
NUTS2 = "nuts2"
NUTS3 = "nuts3"
MUNICIPALITY = "municipality"
PARISH = "parish"

#: The levels of real places, largest first.
LEVELS = (COUNTRY, NUTS1, NUTS2, NUTS3, MUNICIPALITY, PARISH)

ABROAD = "abroad"
UNKNOWN = "unknown"
TOTAL = "total"

#: The kinds of pseudo-unit: rows INE adds that are not places.
PSEUDO_UNITS = (ABROAD, UNKNOWN, TOTAL)


def pseudo_unit(code: str) -> Optional[str]:
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


def _district(code: str) -> Optional[str]:
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


def geo_level(code: str) -> str:
    """The level of a place's code: one of :data:`LEVELS`.

    Raises:
        DataProcessingError: If ``code`` is a pseudo-unit (see :func:`pseudo_unit`) or not a
            code INE issues
    """
    kind = pseudo_unit(code)
    if kind is not None:
        raise DataProcessingError(f"{code!r} is not a place: INE uses it for {kind}")
    n = len(code)
    if n == 1:
        return NUTS1
    if n == 2:
        return COUNTRY if code == "PT" else NUTS2
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


def level_or_pseudo(code: str) -> str:
    """:func:`geo_level`, or the pseudo-unit's kind (``abroad``, ``unknown``, ``total``)."""
    return pseudo_unit(code) or geo_level(code)


def check_codes(codes: Iterable[str]) -> None:
    """Refuse every code that is neither a place nor a pseudo-unit, naming them all."""
    bad = []
    for code in dict.fromkeys(codes):
        try:
            level_or_pseudo(code)
        except DataProcessingError:
            bad.append(code)
    if bad:
        shown = ", ".join(repr(c) for c in bad[:10]) + (" ..." if len(bad) > 10 else "")
        raise DataProcessingError(
            f"{len(bad)} geography code(s) INE is not known to issue: {shown}"
        )
