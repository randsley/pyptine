"""Asynchronous data access with AsyncINE.

AsyncINE fetches data concurrently (httpx), with the same parsing, validation, retries and
provenance as INE, but no cache. INE throttles bursts (it answers 404, which pyptine retries
after 5, 20 and 60 s), so keep concurrency modest.

Indicators used (verified 2026-11-27): 0004167 population by district (annual),
0012234 median value of dwelling sales (quarterly), 0007533 deaths under 1 year (monthly).
Source: Statistics Portugal (INE), https://www.ine.pt.
"""

import asyncio
import time

from pyptine import AsyncINE
from pyptine.utils.exceptions import INERefusalError

REQUESTS = {
    "0004167": {"Dim1": "S7A2025", "Dim2": "PT", "Dim3": "T", "Dim4": "T"},
    "0012234": {"Dim1": "S5A20261", "Dim2": "PT", "Dim3": "H1"},
    "0007533": {"Dim2": "PT"},  # latest month only (no Dim1)
}


def section(title: str) -> None:
    print("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70)


async def main() -> None:
    async with AsyncINE(language="EN") as ine:
        section("1. Several indicators concurrently")
        start = time.perf_counter()
        results = await asyncio.gather(
            *(ine.get_data(code, dims) for code, dims in REQUESTS.items())
        )
        for r in results:
            row = r.data[0]
            print(f"{r.varcd} {row['period']}: {row['value']:,} ({r.unit})")
        print(f"{len(results)} requests in {time.perf_counter() - start:.1f} s")

        section("2. Metadata concurrently")
        metas = await asyncio.gather(*(ine.get_metadata(code) for code in REQUESTS))
        for m in metas:
            print(f"{m.varcd}: {m.periodicity}, {len(m.dimensions)} dimensions")

        section("3. Every period, in batches")
        rows = []
        async for chunk in ine.get_all_data(
            "0004167", {"Dim2": "PT", "Dim3": "T", "Dim4": "T"}, periods_per_request=10
        ):
            rows += chunk.data
            print(
                f"batch {chunk.period_codes[0]}..{chunk.period_codes[-1]}: {len(chunk.data)} rows"
            )
        print(f"{len(rows)} years, {rows[0]['period']}-{rows[-1]['period']}")

        section("4. Refusals")
        try:
            await ine.get_data("9999999")
        except INERefusalError as e:
            print(f"INERefusalError: {e}")


if __name__ == "__main__":
    asyncio.run(main())
