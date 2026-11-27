# TODO

Rewritten 2026-11-27 from an audit of pyptine against the live INE API. The earlier list (January
2026) had gone stale: several items were done, some referred to code that no longer exists
(`CacheManager`, `get_data_paginated`), and the pagination it marked done did not work (INE
ignores `start`/`count`). Ordered by effect on users.

## Done (unreleased, see CHANGELOG)

- Data rows carry `period` and `period_code`; INE's period key was dropped before.
- INE refusals (HTTP 200 with `Sucesso.Falso`) raise `INERefusalError` and are not cached.
- `get_all_data` fetches periods in batches; `chunk_size` deprecated.
- Several codes per dimension (list or comma-separated); latest-period-only default documented.
- Dimension validation uses `cat_id`, and names the right code for a label or `categ_cod`.
- Analysis methods work on real data (period codes, one series, true year-over-year); plots order by period.
- Weekly API-drift check; offline tests pinned against recorded INE responses.
- Retries (404 throttling, 429 with Retry-After, 5xx, resets, malformed bodies; 5/20/60 s) and a 130 s read timeout, sync and async.
- Earlier: `get_data` always returns `DataResponse`; `search` takes `theme`; centralised CLI errors.

## Open

1. **Provenance in what is written.** `extraction_date` is the parse time (wrong for a cached response): use INE's `DataExtracao`. The CSV header lacks the period(s), the dimension filter, the request URL and INE's licence credit; `INE.export_csv` also omits the date and language. `INE(cache_ttl=...)` is accepted and ignored.
2. **Catalogue.** `<keywords>` (several CDATA sections) is never parsed, so `Indicator.keywords` is always empty. `get_main_indicators` (`opc=3`) returns Portuguese titles under `EN`; document it.
3. **Metadata fields never sent.** `IndicatorMetadata.source`, `notes`, `description`, `theme`, `subtheme`, `geo_last_level` and the URLs are read from keys INE's metadata endpoint does not send, so they are always None. Fill them from the catalogue (`opc=1`) or drop them.
4. **Geography.** Rows mix geographic levels (country, NUTS, municipalities). A `level` column from the `geocod` length would help; see PortugalINE.jl's "Geography traps" for the code forms (bare Census codes, NUTS III codes with letters, pseudo-units).
5. **Examples** (`src/examples/`) use indicator `0004127`, which is not in INE's catalogue, and run the analysis methods on unfiltered data. Rewrite them against verified indicators.
6. **Old-format parsing paths.** The parsers still accept a format INE does not serve (`dados`/`periodo`/`dimensoes`/`codigo`), and many older tests use hand-written fixtures in it. Move those tests to the recorded responses, then remove the old paths.
7. **Package metadata.** `pyproject.toml` URLs point to `github.com/nigelrandsley/pyptine` (404; the repo is `randsley/pyptine`); the author email is not the public contact; 0.3.3 is tagged but not on PyPI.
8. **CI.** Update actions (checkout v3, setup-python v4, codecov v3); Python 3.9 is past end of life; consider a scheduled run with live tests (the `live` marker is declared but unused).
9. **Smaller.**
    - The CLI's `search --timeout` defaults to 10 s (the catalogue download takes ~20 s; the timeout is per read, so it works, but the help text says "Request timeout").
    - `INE` builds four clients, each opening its own `DiskCache`; share one.
    - `pivot_by_dimension`/`aggregate_by_period` default `value_column="valor"`, but rows hold `value`; list handling of `value_column`.
    - `merge_json_files`' `merge_key` is unused.
    - `type: ignore` at `search/catalog.py` (`get_recently_updated`); redundant `cast` calls in `client/base.py`.
    - `COMPATIBILITY_SUMMARY.md`, `GITHUB_CHECKLIST.md`, `PROJECT_SUMMARY.md`, `PYTHON_3.14_COMPATIBILITY.md` may be stale.
