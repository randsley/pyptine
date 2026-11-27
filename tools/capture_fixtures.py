"""Capture real INE responses as offline test fixtures.

Run from the repo root:  python tools/capture_fixtures.py

Writes each response below to tests/fixtures/ine/<name> exactly as INE served it (bytes
unchanged), and tests/fixtures/ine/PROVENANCE.json with the URL, capture date and SHA-256 of
each. Tests pin facts against these files, never against hand-written examples. Recapturing
changes the files: review the diff, and update the pinned facts that moved.

Responses are filtered to stay small. The indicators: 0008273 resident population by place of
residence, sex and age group (annual; Dim2 geography, Dim3 sex, Dim4 age group); 0012234 median
value of dwelling sales (quarterly; Dim3 cat_id H1 versus categ_cod H.1); 0008234 live births by
mother's residence (annual; every geographic level and the pseudo-units).

Capture some only, keeping the others and their PROVENANCE entries:
    python tools/capture_fixtures.py --only data_0008234_S7A2023_geo_EN.json
Source: Statistics Portugal (INE), https://www.ine.pt. Needs only requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import time
from datetime import datetime, timezone

import requests

OUT = pathlib.Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "ine"
UA = {"User-Agent": "pyptine-fixtures (+https://github.com/randsley/pyptine)"}
META = "https://www.ine.pt/ine/json_indicador/pindicaMeta.jsp"
DATA = "https://www.ine.pt/ine/json_indicador/pindica.jsp"
CATALOGUE = "https://www.ine.pt/ine/xml_indic.jsp"
TOTAL = {"Dim2": "PT", "Dim3": "T", "Dim4": "T"}

# name -> (url, params, what it pins)
FIXTURES: dict[str, tuple[str, dict[str, str], str]] = {
    "meta_0008273_EN.json": (META, {"varcd": "0008273", "lang": "EN"}, "metadata, 4 dimensions"),
    "meta_0008273_PT.json": (META, {"varcd": "0008273", "lang": "PT"}, "the same, Portuguese"),
    "meta_0012234_EN.json": (
        META,
        {"varcd": "0012234", "lang": "EN"},
        "cat_id H1 vs categ_cod H.1",
    ),
    "meta_9999999_EN.json": (
        META,
        {"varcd": "9999999", "lang": "EN"},
        "unknown code: HTTP 200 + Falso",
    ),
    "data_0008273_latest_EN.json": (
        DATA,
        {"op": "2", "varcd": "0008273", "lang": "EN", **TOTAL},
        "no Dim1: latest period only",
    ),
    "data_0008273_S7A2021_S7A2022_EN.json": (
        DATA,
        {"op": "2", "varcd": "0008273", "lang": "EN", "Dim1": "S7A2021,S7A2022", **TOTAL},
        "two periods, Dados keyed by label",
    ),
    "data_0008273_S7A2021_S7A2022_PT.json": (
        DATA,
        {"op": "2", "varcd": "0008273", "lang": "PT", "Dim1": "S7A2021,S7A2022", **TOTAL},
        "the same, Portuguese",
    ),
    "data_0008273_count1_EN.json": (
        DATA,
        {
            "op": "2",
            "varcd": "0008273",
            "lang": "EN",
            "Dim1": "S7A2021,S7A2022",
            "start": "0",
            "count": "1",
            **TOTAL,
        },
        "start/count ignored: still 2 rows",
    ),
    "data_0008273_S7A2022_levels_EN.json": (
        DATA,
        {"op": "2", "varcd": "0008273", "lang": "EN", "Dim1": "S7A2022", "Dim3": "T", "Dim4": "T"},
        "every geographic level in one response",
    ),
    "data_0008273_Dim1label_EN.json": (
        DATA,
        {"op": "2", "varcd": "0008273", "lang": "EN", "Dim1": "2022"},
        "period label in Dim1: refused (Cod 4)",
    ),
    "data_9999999_EN.json": (
        DATA,
        {"op": "2", "varcd": "9999999", "lang": "EN"},
        "unknown code: HTTP 200 + Falso",
    ),
    "data_0012234_H1_EN.json": (
        DATA,
        {
            "op": "2",
            "varcd": "0012234",
            "lang": "EN",
            "Dim1": "S5A20261",
            "Dim2": "PT",
            "Dim3": "H1",
        },
        "cat_id accepted",
    ),
    "data_0012234_Hdot1_EN.json": (
        DATA,
        {
            "op": "2",
            "varcd": "0012234",
            "lang": "EN",
            "Dim1": "S5A20261",
            "Dim2": "PT",
            "Dim3": "H.1",
        },
        "categ_cod refused",
    ),
    "data_0008234_S7A2023_geo_EN.json": (
        DATA,
        {
            "op": "2",
            "varcd": "0008234",
            "lang": "EN",
            "Dim1": "S7A2023",
            "Dim2": "T,PT,1,11,11A,11A0104,11A010402,1120302FA,2004301,"
            "YY,0,00,000,0000000,000000000,9,99,999,9999999,999999999",
            "Dim3": "T",
        },
        "live births: every geographic level and INE's pseudo-units (total, abroad, unknown)",
    ),
    "meta_0004167_EN.json": (
        META,
        {"varcd": "0004167", "lang": "EN"},
        "geography by district (Distrito/ Regiao): 2-digit codes are districts, not NUTS II",
    ),
    "data_0004167_S7A2025_EN.json": (
        DATA,
        {"op": "2", "varcd": "0004167", "lang": "EN", "Dim1": "S7A2025", "Dim3": "T", "Dim4": "T"},
        "one year at every district and region",
    ),
    "catalogue_opc1_0008273_EN.xml": (
        CATALOGUE,
        {"opc": "1", "varcd": "0008273", "lang": "EN"},
        "one catalogue entry",
    ),
    "catalogue_opc1_0008273_PT.xml": (
        CATALOGUE,
        {"opc": "1", "varcd": "0008273", "lang": "PT"},
        "the same, Portuguese",
    ),
    "catalogue_opc1_9999999_EN.xml": (
        CATALOGUE,
        {"opc": "1", "varcd": "9999999", "lang": "EN"},
        "unknown code: HTTP 200 and a Java stack trace, not XML",
    ),
}
# Not captured: opc=3 (488 kB) and opc=2 (13,290 indicators). tools/check_api.py checks them live.


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--only", nargs="+", metavar="NAME", help="capture these fixtures only")
    args = parser.parse_args()
    unknown = sorted(set(args.only or []) - FIXTURES.keys())
    if unknown:
        parser.error(f"unknown fixture(s): {', '.join(unknown)}")

    OUT.mkdir(parents=True, exist_ok=True)
    provenance: dict = {
        "source": "Statistics Portugal (INE), https://www.ine.pt",
        "captured_by": "tools/capture_fixtures.py",
        "files": {},
    }
    index = OUT / "PROVENANCE.json"
    if args.only and index.exists():
        provenance = json.loads(index.read_text(encoding="utf-8"))
    for name in args.only or list(FIXTURES):
        url, params, purpose = FIXTURES[name]
        time.sleep(1.0)  # INE answers bursts with 404
        r = requests.get(url, params=params, headers=UA, timeout=300)
        r.raise_for_status()
        (OUT / name).write_bytes(r.content)
        provenance["files"][name] = {
            "url": r.url,
            "purpose": purpose,
            "captured": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sha256": hashlib.sha256(r.content).hexdigest(),
            "bytes": len(r.content),
        }
        print(f"{name}: {len(r.content)} bytes")
    (OUT / "PROVENANCE.json").write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
