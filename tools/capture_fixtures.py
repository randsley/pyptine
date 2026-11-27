"""Capture real INE responses as offline test fixtures.

Run from the repo root:  python tools/capture_fixtures.py

Writes each response below to tests/fixtures/ine/<name> exactly as INE served it (bytes
unchanged), and tests/fixtures/ine/PROVENANCE.json with the URL, capture date and SHA-256 of
each. Tests pin facts against these files, never against hand-written examples. Recapturing
changes the files: review the diff, and update the pinned facts that moved.

Responses are filtered to stay small. The indicators: 0008273 resident population by place of
residence, sex and age group (annual; Dim2 geography, Dim3 sex, Dim4 age group); 0012234 median
value of dwelling sales (quarterly; Dim3 cat_id H1 versus categ_cod H.1).
Source: Statistics Portugal (INE), https://www.ine.pt. Needs only requests.
"""

from __future__ import annotations

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
}
# Not captured: opc=3 (488 kB) and opc=2 (13,290 indicators). tools/check_api.py checks them live.


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    provenance = {
        "source": "Statistics Portugal (INE), https://www.ine.pt",
        "captured_by": "tools/capture_fixtures.py",
        "files": {},
    }
    for name, (url, params, purpose) in FIXTURES.items():
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
