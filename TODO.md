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
- Provenance in what is written: INE's `DataExtracao`, request URL, filters, periods, licence and citation in `DataResponse`, the CSV header and JSON; `cache_ttl` honoured. Excel export with a Provenance sheet; the catalogue's date is INE's.
- Examples rewritten against verified indicators; each runs end to end.
- Old-format parsing removed; every test runs on recorded INE responses.
- Package metadata: repository URLs, public contact email, one version source (0.4.0.dev0), CITATION.cff; the wheel no longer installs a top-level `examples`.
- District geographies (0004167) told from NUTS; the catalogue's Java error page for unknown codes refused at once.
- Geographic levels from codes: `DataResponse.with_geo_level()` / `at_level()`.
- Metadata's catalogue-only fields filled from `opc=1` by `INE.get_metadata`; `notes` removed.
- Catalogue keywords parsed (split on CDATA boundaries); `opc=3`'s Portuguese titles documented.
- Earlier: `get_data` always returns `DataResponse`; `search` takes `theme`; centralised CLI errors.

## Open

1. **CI.** Update actions (checkout v3, setup-python v4, codecov v3); Python 3.9 is past end of life; consider a scheduled run with live tests (the `live` marker is declared but unused).
2. **Smaller.**
    - The CLI's `search --timeout` defaults to 10 s (the catalogue download takes ~20 s; the timeout is per read, so it works, but the help text says "Request timeout").
    - The catalogue's `<extraction_date>` has no timezone. On 2026-11-27 it matched the data endpoint's UTC `DataExtracao`, but Lisbon is UTC+0 then: compare again in summer (after 2027-03-28) to learn whether it is UTC or Lisbon time.
    - `ine_citation` lists every period; a long contiguous run (27 quarters) could be written as a range.
    - `INE` builds four clients, each opening its own `DiskCache`; share one.
    - `pivot_by_dimension`/`aggregate_by_period` default `value_column="valor"`, but rows hold `value`; list handling of `value_column`.
    - `merge_json_files`' `merge_key` is unused.
    - `type: ignore` at `search/catalog.py` (`get_recently_updated`); redundant `cast` calls in `client/base.py`.
    - `COMPATIBILITY_SUMMARY.md`, `GITHUB_CHECKLIST.md`, `PROJECT_SUMMARY.md`, `PYTHON_3.14_COMPATIBILITY.md` may be stale.
