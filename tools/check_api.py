"""Check that INE's API still returns what pyptine reads.

Run from the repo root:

    python tools/check_api.py                    # check
    python tools/check_api.py --update-baseline  # accept reported changes

INE publishes no machine-readable spec, so this checks live responses against the fields
pyptine's parsers read (src/pyptine/client/{metadata,data,catalogue}.py), with the types they
expect. It reads the raw JSON and XML, not pyptine's parsed models, so a failure names the field
that moved, not the parser that tripped over it. Same design as PortugalINE.jl's
tools/check_api.jl:

1. Shape: each endpoint's response holds every field the client reads (READ_* below; keep them
   in step with the parsers).
2. Behaviour: the traps documented in CLAUDE.md still hold (failure as HTTP 200, Dados keyed by
   the period label, latest period only when unfiltered, comma-separated Dim1, cat_id versus
   categ_cod, start/count ignored, opc=3 in Portuguese under either language).
3. Drift: keys and catalogue sizes compared with tools/api_baseline.json. Changes there are
   warnings, never errors.

Exit status 1 if anything the client relies on is missing or has changed meaning. Runs weekly
in GitHub Actions (.github/workflows/api-check.yml), where problems also appear as annotations.
Needs only requests.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any
from xml.etree import ElementTree as ET

import requests

BASE = "https://www.ine.pt/ine"
META = f"{BASE}/json_indicador/pindicaMeta.jsp"
DATA = f"{BASE}/json_indicador/pindica.jsp"
CATALOGUE = f"{BASE}/xml_indic.jsp"
BASELINE = pathlib.Path(__file__).with_name("api_baseline.json")
UA = {"User-Agent": "pyptine-api-check (+https://github.com/randsley/pyptine)"}
IN_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"

# INE answers 404 for valid codes when it throttles, resets connections under load, and its
# gateway gives 504 after 120 s. An unknown code is HTTP 200 with Sucesso.Falso, never 404.
RETRY_STATUS = {404, 429, 500, 502, 503, 504}
RETRY_DELAYS = (5, 20, 60)
PAUSE = 1.0  # between requests, to stay clear of INE's throttling

# What the parsers read. "str": a JSON string; "strint": a string holding an integer (INE sends
# numbers as strings); "obj"/"list": a JSON object/array; "valor": a string or null; "date": a
# YYYY-MM-DD string (MetadataClient parses it). A trailing "?" marks a field that may be absent.
READ_META = {
    "IndicadorCod": "str",
    "IndicadorNome": "str",
    "Lingua": "str",
    "UnidadeMedida": "str",
    "Periodic": "str",
    "UltimoPeriodo": "str",
    "DataUltimaAtualizacao": "date",
    "Dimensoes": "obj",
    "Sucesso": "obj",
}
READ_DESC = {"dim_num": "strint", "abrv": "str", "nota_dsg": "str?"}
READ_CATEG = {
    "dim_num": "strint",
    "cat_id": "str",
    "categ_cod": "str",
    "categ_dsg": "str",
    "categ_ord": "strint",
}
READ_DATA = {
    "IndicadorCod": "str",
    "IndicadorDsg": "str",
    "DataExtracao": "str",
    "DataUltimoAtualizacao": "str",
    "MetaInfUrl": "str",
    "Dados": "obj",
    "Sucesso": "obj",
}
READ_ROW = {"geocod": "str", "geodsg": "str", "valor": "valor", "ind_string": "str?"}
READ_FAIL = {"Msg": "str", "Cod": "strint"}
# CatalogueClient._parse_indicator_xml: <indicator> children, then nested ones.
READ_CAT = [
    "varcd",
    "title",
    "theme",
    "subtheme",
    "keywords",
    "description",
    "geo_lastlevel",
    "source",
    "periodicity",
    "dates/last_period_available",
    "dates/last_update",
    "html/bdd_url",
    "json/json_dataset",
    "json/json_metainfo",
]
# MetadataClient reads these too, but INE has not sent them (checked 2026-11-27); they are always
# None in IndicatorMetadata. Seeing one appear is worth a warning.
UNSENT_META = [
    "Fonte",
    "Nota",
    "Descricao",
    "Tema",
    "Subtema",
    "GeoUltimoNivel",
    "UrlHtml",
    "UrlMeta",
    "UrlDados",
]

# The indicators used. 0008273 (resident population by place of residence, sex and age group;
# annual) has four dimensions; filtering Dim2-Dim4 to the country total keeps responses small.
POP = "0008273"
POP_TOTAL = {"Dim2": "PT", "Dim3": "T", "Dim4": "T"}
# 0012234 (median value of dwelling sales; quarterly) has a Dim3 whose cat_id and categ_cod
# differ (H1 versus H.1).
SALES = "0012234"

errors: list[str] = []
warnings: list[str] = []
seen: dict[str, Any] = {}


def error(msg: str) -> None:
    errors.append(msg)
    print(f"::error::{msg}" if IN_ACTIONS else f"ERROR    {msg}")


def warn(msg: str) -> None:
    warnings.append(msg)
    print(f"::warning::{msg}" if IN_ACTIONS else f"WARNING  {msg}")


def ok(msg: str) -> None:
    print(f"ok       {msg}")


def fetch(url: str, **params: str) -> requests.Response:
    """One live GET, retried on throttling (404), 429, 5xx and a failed connection."""
    last = ""
    for delay in (*RETRY_DELAYS, None):
        time.sleep(PAUSE)
        try:
            r = requests.get(url, params=params, headers=UA, timeout=300)
            if r.status_code not in RETRY_STATUS:
                r.raise_for_status()
                return r
            last = f"HTTP {r.status_code}"
        except requests.ConnectionError as e:
            last = f"connection failed ({e})"
        if delay is None:
            break
        print(f"retry    {url} {params}: {last}; waiting {delay} s")
        time.sleep(delay)
    raise RuntimeError(f"{url} {params}: {last} after {len(RETRY_DELAYS)} retries")


def get_json(url: str, **params: str) -> dict[str, Any]:
    """Fetch a JSON endpoint and unwrap INE's one-element array."""
    body = fetch(url, **params).json()
    if isinstance(body, list):
        if len(body) != 1 or not isinstance(body[0], dict):
            raise RuntimeError(f"{url} {params}: a list of {len(body)}, expected one object")
        return body[0]
    error(f"{url} {params}: the response is no longer a one-element array")
    return body


def is_type(value: Any, kind: str) -> bool:
    kind = kind.rstrip("?")
    if kind == "str":
        return isinstance(value, str)
    if kind == "strint":
        return isinstance(value, int) or (isinstance(value, str) and value.lstrip("-").isdigit())
    if kind == "obj":
        return isinstance(value, dict)
    if kind == "valor":
        return value is None or isinstance(value, str)
    if kind == "date":
        try:
            datetime.strptime(value, "%Y-%m-%d")
            return True
        except (TypeError, ValueError):
            return False
    raise ValueError(f"unknown type {kind}")


def field_problems(obj: dict[str, Any], fields: dict[str, str]) -> list[str]:
    bad = []
    for key, kind in fields.items():
        if key not in obj:
            if not kind.endswith("?"):
                bad.append(f"{key} missing")
        elif not is_type(obj[key], kind):
            bad.append(f"{key} is {obj[key]!r}, expected {kind.rstrip('?')}")
    return bad


def check_fields(where: str, obj: dict[str, Any], fields: dict[str, str]) -> bool:
    bad = field_problems(obj, fields)
    if bad:
        error(f"{where}: " + "; ".join(bad))
    else:
        ok(f"{where}: all {len(fields)} fields the client reads")
    return not bad


def succeeded(obj: dict[str, Any]) -> bool:
    return "Verdadeiro" in (obj.get("Sucesso") or {})


def refused(obj: dict[str, Any]) -> bool:
    return bool((obj.get("Sucesso") or {}).get("Falso"))


def categories(meta: dict[str, Any]) -> list[dict[str, Any]]:
    """Every category in Categoria_Dim, checking the key form MetadataClient splits."""
    out = []
    for block in meta["Dimensoes"]["Categoria_Dim"]:
        for key, cats in block.items():
            m = re.match(r"^Dim_Num(\d+)_", key)
            if not m or not all(str(c.get("dim_num")) == m.group(1) for c in cats):
                error(
                    f"metadata: Categoria_Dim key {key!r} is not Dim_Num<n>_<code> with n the "
                    "categories' dim_num; MetadataClient takes the dimension from the key"
                )
                continue
            out.extend(cats)
    return out


def rows_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for rows in data["Dados"].values() for r in rows]


# ---------------------------------------------------------------- 1 and 2: shape and behaviour


def check_metadata() -> dict[str, str] | None:
    meta = get_json(META, varcd=POP, lang="EN")
    if not check_fields(f"metadata {POP}", meta, READ_META):
        return None
    if not succeeded(meta):
        error(f"metadata {POP}: Sucesso holds no Verdadeiro")
    seen["metadata"] = sorted(meta)
    sent = [k for k in UNSENT_META if k in meta]
    if sent:
        warn(f"metadata {POP}: INE now sends {', '.join(sent)}, which MetadataClient reads")
    dz = meta["Dimensoes"]
    for key in ("Descricao_Dim", "Categoria_Dim"):
        if key not in dz:
            error(f"metadata {POP}: Dimensoes.{key} missing")
            return None
    for d in dz["Descricao_Dim"]:
        check_fields(f"metadata {POP} Descricao_Dim[{d.get('dim_num')}]", d, READ_DESC)
    seen["metadata_dimension"] = sorted(dz["Descricao_Dim"][0])
    nums = sorted(int(d["dim_num"]) for d in dz["Descricao_Dim"])
    if nums == [1, 2, 3, 4]:
        ok(f"metadata {POP}: dimensions 1-4")
    else:
        error(f"metadata {POP}: dimensions {nums}, expected [1, 2, 3, 4]")
    cats = categories(meta)
    bad = [c for c in cats if field_problems(c, READ_CATEG)]
    if bad:
        error(
            f"metadata {POP}: {len(bad)} of {len(cats)} categories lack a field in "
            + ", ".join(READ_CATEG)
        )
    else:
        ok(f"metadata {POP}: all {len(cats)} categories carry the fields read")
    seen["metadata_category"] = sorted(cats[0])
    # Dim1 is the period and Dim2 geography, judged by codes, never by the English labels.
    periods = [c for c in cats if str(c["dim_num"]) == "1"]
    geo = {c["categ_cod"] for c in cats if str(c["dim_num"]) == "2"}
    if periods and all(re.fullmatch(r"S\d+A\d+", c["cat_id"]) for c in periods):
        ok(f"metadata {POP}: Dim1 is the period ({len(periods)} codes of the form S7A2023)")
    else:
        error(f"metadata {POP}: Dim1 codes are no longer period codes (S7A<year>)")
    if "PT" in geo:
        ok(f"metadata {POP}: Dim2 is geography (holds PT)")
    else:
        error(f"metadata {POP}: Dim2 no longer holds PT")
    latest = max(periods, key=lambda c: int(c["categ_ord"]))
    return {"code": latest["cat_id"], "label": latest["categ_dsg"]}


def check_data(period: dict[str, str]) -> None:
    # Filtered by the period code, keyed by the period label.
    data = get_json(DATA, op="2", varcd=POP, lang="EN", Dim1=period["code"], **POP_TOTAL)
    if not check_fields(f"data {POP}", data, READ_DATA):
        return
    seen["data"] = sorted(data)
    keys = list(data["Dados"])
    if keys == [period["label"]] and period["code"] != period["label"]:
        ok(f"data {POP}: filtered by Dim1={period['code']}, keyed by {period['label']!r}")
    else:
        error(
            f"data {POP}: Dados keys are {keys} for Dim1={period['code']}; expected "
            f"[{period['label']!r}]"
        )
    rows = rows_of(data)
    bad = [r for r in rows if field_problems(r, READ_ROW)]
    if bad or not rows:
        error(f"data {POP}: {len(bad)} of {len(rows)} rows lack geocod, geodsg or valor")
    else:
        ok(f"data {POP}: all {len(rows)} row(s) carry geocod, geodsg, valor")
        seen["data_row"] = sorted(rows[0])
    # valor is the machine form (decimal point); ind_string is the localised display form.
    if any("," in r["valor"] for r in rows if isinstance(r.get("valor"), str)):
        error(f"data {POP}: valor carries a decimal comma")
    if rows and all("dim_3" in r and "dim_3_t" in r for r in rows):
        ok(f"data {POP}: rows carry extra dimensions as dim_3 / dim_3_t")
    else:
        error(f"data {POP}: rows no longer carry dim_3 and dim_3_t")


def check_periods() -> None:
    # Unfiltered by period: the latest period only.
    data = get_json(DATA, op="2", varcd=POP, lang="EN", **POP_TOTAL)
    keys = list(data.get("Dados") or {})
    if len(keys) == 1:
        ok(f"data {POP} without Dim1: latest period only ({keys[0]})")
    else:
        warn(f"data {POP} without Dim1: now {len(keys)} periods; the docs say latest only")
    # Several periods: one comma-separated Dim1. And start/count are ignored, which is why
    # DataClient.get_all_data cannot paginate (count=1 still returns both rows).
    data = get_json(
        DATA,
        op="2",
        varcd=POP,
        lang="EN",
        Dim1="S7A2021,S7A2022",
        start="0",
        count="1",
        **POP_TOTAL,
    )
    keys = sorted(data.get("Dados") or {})
    if keys == ["2021", "2022"]:
        ok(f"data {POP} Dim1=S7A2021,S7A2022: both periods")
    else:
        error(f"data {POP} Dim1=S7A2021,S7A2022: got {keys}; expected ['2021', '2022']")
    n = len(rows_of(data)) if keys else 0
    if n == 2:
        ok(f"data {POP} start=0&count=1: ignored (2 rows)")
    else:
        warn(f"data {POP} start=0&count=1: {n} row(s); INE may now paginate, see get_all_data")


def check_category_codes() -> None:
    meta = get_json(META, varcd=SALES, lang="EN")
    cats = categories(meta)
    differ = [c for c in cats if c["cat_id"] != c["categ_cod"]]
    if not differ:
        warn(f"metadata {SALES}: cat_id and categ_cod no longer differ anywhere")
        return
    c = differ[0]
    dim = f"Dim{c['dim_num']}"
    periods = [p for p in cats if str(p["dim_num"]) == "1"]
    period = max(periods, key=lambda p: int(p["categ_ord"]))["cat_id"]
    base = {"op": "2", "varcd": SALES, "lang": "EN", "Dim1": period, "Dim2": "PT"}
    by_id = get_json(DATA, **base, **{dim: c["cat_id"]})
    by_cod = get_json(DATA, **base, **{dim: c["categ_cod"]})
    if succeeded(by_id) and refused(by_cod):
        ok(
            f"data {SALES}: {dim} takes cat_id {c['cat_id']!r} and refuses categ_cod "
            f"{c['categ_cod']!r}"
        )
    else:
        error(
            f"data {SALES}: {dim}={c['cat_id']!r} (cat_id) succeeded={succeeded(by_id)}, "
            f"{dim}={c['categ_cod']!r} (categ_cod) refused={refused(by_cod)}; "
            "expected cat_id accepted and categ_cod refused"
        )


def check_failures() -> None:
    # INE refuses with HTTP 200 and Sucesso.Falso: an unknown code, and a period label in Dim1.
    for where, url, params in [
        ("metadata 9999999", META, {"varcd": "9999999", "lang": "EN"}),
        ("data 9999999", DATA, {"op": "2", "varcd": "9999999", "lang": "EN"}),
        (f"data {POP} Dim1=2022", DATA, {"op": "2", "varcd": POP, "lang": "EN", "Dim1": "2022"}),
    ]:
        try:
            obj = get_json(url, **params)
        except Exception as e:  # noqa: BLE001 - report whatever INE did instead
            error(f"{where}: the request failed ({e}); INE used to answer with HTTP 200")
            continue
        if refused(obj):
            check_fields(f"{where}: refusal (HTTP 200)", obj["Sucesso"]["Falso"][0], READ_FAIL)
        else:
            error(f"{where}: no Sucesso.Falso; INE used to refuse this with HTTP 200")
    # The catalogue answers an unknown code with HTTP 200 and a Java stack trace (not XML),
    # which pyptine.client.parsing.check_catalogue_body refuses.
    where = "catalogue opc=1 9999999"
    body = fetch(CATALOGUE, opc="1", varcd="9999999", lang="EN").content
    if re.match(rb"^\s*<pre>\s*java\.[\w.$]+(Exception|Error)", body[:500]):
        ok(f"{where}: Java error page (HTTP 200), as pyptine expects")
    else:
        error(f"{where}: no longer the Java error page; got {body[:80]!r}")


# The catalogue's <extraction_date>, which CatalogueResponse needs (pyptine.provenance
# parse_catalogue_date): one English form under either language, no timezone.
CATALOGUE_DATE = re.compile(
    r"^[A-Z][a-z]+, \d{1,2} [A-Z][a-z]+ \d{4}, \d{2}:\d{2}:\d{2}(\.\d{1,6})? (AM|PM)$"
)


KEYWORDS_SHAPE = re.compile(r"^INE(,<!\[CDATA\[.*?\]\]>)+$", re.S)


def parse_catalogue(where: str, xml: str) -> list[ET.Element]:
    root = ET.fromstring(xml)
    if root.tag != "catalog":
        error(f"{where}: root element is <{root.tag}>, expected <catalog>")
    date = (root.findtext("extraction_date") or "").strip()
    if not CATALOGUE_DATE.match(date):
        error(
            f"{where}: <extraction_date> {date!r} is not 'Friday, 27 November 2026, "
            "04:17:58.067 PM', which CatalogueResponse parses"
        )
    inds = root.findall(".//indicator")
    # CatalogueClient splits <keywords> on its raw CDATA boundaries, matched to each
    # <indicator id=...> by a regex over the raw XML: check that shape.
    raw = re.findall(r"<keywords>(.*?)</keywords>", xml, re.S)
    ids = re.findall(r'<indicator\b[^>]*?\bid="([^"]*)"', xml)
    if len(raw) != len(inds) or len(ids) != len(inds):
        error(
            f"{where}: {len(inds)} <indicator>, {len(ids)} with an id attribute, {len(raw)} "
            "<keywords>; CatalogueClient expects one of each per indicator"
        )
    odd = [k for k in raw if not KEYWORDS_SHAPE.match(k.strip())]
    if odd:
        error(f"{where}: {len(odd)} <keywords> not 'INE,<![CDATA[..]]>,...', e.g. {odd[0][:80]!r}")
    return inds


def check_catalogue_entry() -> None:
    where = f"catalogue opc=1 {POP}"
    inds = parse_catalogue(where, fetch(CATALOGUE, opc="1", varcd=POP, lang="EN").text)
    if len(inds) != 1:
        error(f"{where}: {len(inds)} <indicator> elements, expected 1")
        return
    ind = inds[0]
    missing = [t for t in READ_CAT if ind.find(t) is None]
    if missing:
        error(f"{where}: missing tag(s) " + ", ".join(missing))
    else:
        ok(f"{where}: all {len(READ_CAT)} tags the client reads")
    seen["catalogue_tags"] = sorted({child.tag for child in ind})
    date = (ind.findtext("dates/last_update") or "").strip()
    try:
        datetime.strptime(date, "%d-%m-%Y")
        ok(f"{where}: last_update is DD-MM-YYYY ({date})")
    except ValueError:
        error(f"{where}: last_update {date!r} is not DD-MM-YYYY, which CatalogueClient parses")


def check_catalogue_groups() -> None:
    # opc=3 (the main group) localises only theme and subtheme: its titles are Portuguese under
    # either language. Compared by code, not by guessing the language of the text.
    titles = {}
    for lang in ("EN", "PT"):
        inds = parse_catalogue(f"catalogue opc=3 {lang}", fetch(CATALOGUE, opc="3", lang=lang).text)
        titles[lang] = {i.findtext("varcd"): i.findtext("title") for i in inds}
    common = titles["EN"].keys() & titles["PT"].keys()
    same = sum(titles["EN"][k] == titles["PT"][k] for k in common)
    seen["catalogue_opc3_size"] = len(titles["EN"])
    ok(f"catalogue opc=3: {len(titles['EN'])} indicators")
    if common and same == len(common):
        ok("catalogue opc=3: titles identical under EN and PT (Portuguese under either)")
    else:
        warn(f"catalogue opc=3: {len(common) - same} of {len(common)} titles now differ by lang")
    # opc=2, the complete catalogue that CatalogueBrowser.search reads.
    inds = parse_catalogue("catalogue opc=2 EN", fetch(CATALOGUE, opc="2", lang="EN").text)
    seen["catalogue_opc2_size"] = len(inds)
    without = sum(1 for i in inds if not (i.findtext("varcd") or "").strip())
    if inds and not without:
        ok(f"catalogue opc=2: {len(inds)} indicators, each with a varcd")
    else:
        error(f"catalogue opc=2: {len(inds)} indicators, {without} without a varcd")


# ---------------------------------------------------------------- 3: drift against the baseline


def compare_baseline(update: bool) -> None:
    if update:
        BASELINE.write_text(json.dumps(seen, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"baseline updated: {BASELINE}")
        return
    if not BASELINE.exists():
        warn(f"no baseline at {BASELINE.name}; create one with --update-baseline")
        return
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    for key, now in seen.items():
        if key not in base:
            warn(f"baseline has no entry for {key}")
            continue
        was = base[key]
        if isinstance(now, list):
            added, removed = sorted(set(now) - set(was)), sorted(set(was) - set(now))
            if added:
                warn(f"{key}: new value(s) " + ", ".join(added))
            if removed:
                warn(f"{key}: value(s) gone " + ", ".join(removed))
        elif now != was:
            warn(f"{key} changed: {was} -> {now}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--update-baseline", action="store_true", help="accept the current state")
    args = ap.parse_args()

    def metadata_then_data() -> None:
        period = check_metadata()
        if period is not None:
            check_data(period)

    checks: list[Callable[[], None]] = [
        metadata_then_data,
        check_periods,
        check_category_codes,
        check_failures,
        check_catalogue_entry,
        check_catalogue_groups,
    ]
    for check in checks:
        try:
            check()
        except Exception as e:  # noqa: BLE001 - one broken check must not hide the others
            error(f"{check.__name__}: unexpected failure: {type(e).__name__}: {e}")
    if args.update_baseline and errors:
        print("not updating the baseline: there are errors")
    else:
        compare_baseline(args.update_baseline)
    print()
    print(f"{len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
