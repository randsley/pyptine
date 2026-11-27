"""Time-series analysis with pyptine's built-in methods.

The analysis methods work on **one series**: one row per period. INE returns every region,
category and (without Dim1) only the latest period, so pin every dimension but the period,
and ask for the periods themselves. Each method refuses data with several rows per period.

Indicators used (verified 2026-11-27):
- 0004167 resident population, Portugal, both sexes, all ages (annual, 1991-2025)
- 0012234 median value of dwelling sales, Portugal, all dwellings (quarterly, 2019Q4-)
- 0007533 deaths under 1 year, Portugal (monthly, 2026)

Source: Statistics Portugal (INE), https://www.ine.pt.
"""

from pyptine import INE
from pyptine.models.response import DataResponse

ine = INE(language="EN")


def series(varcd: str, **filters: str) -> DataResponse:
    """Every period of one series: all Dim1 codes, every other dimension pinned."""
    periods = [v.code for v in ine.get_dimensions(varcd)[0].values]
    return ine.get_data(varcd, {"Dim1": periods, **filters})


def section(title: str) -> None:
    print("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70)


population = series("0004167", Dim2="PT", Dim3="T", Dim4="T")
prices = series("0012234", Dim2="PT", Dim3="H1")
infant_deaths = series("0007533", Dim2="PT")

section("1. Year-over-year growth (annual)")
df = population.calculate_yoy_growth().to_dataframe()
print(df[["period", "value", "yoy_growth"]].tail(8).to_string(index=False))

section("2. Year-over-year growth (quarterly: each quarter against the same quarter)")
df = prices.calculate_yoy_growth().to_dataframe()
print(df[["period", "value", "yoy_growth"]].tail(6).to_string(index=False))

section("3. Month-over-month change")
df = infant_deaths.calculate_mom_change().to_dataframe()
print(df[["period", "value", "mom_change"]].to_string(index=False))

section("4. Moving averages")
df = population.calculate_moving_average(window=5).to_dataframe()
ema = population.calculate_exponential_moving_average(span=5).to_dataframe()
df["ema"] = ema["ema"]
print(df[["period", "value", "moving_avg", "ema"]].tail(6).to_string(index=False))

section("5. Chained analysis, exported with provenance")
result = prices.calculate_yoy_growth().calculate_moving_average(window=4)
result.to_csv("dwelling_prices_analysis.csv")  # provenance kept through the analysis
print(result.to_dataframe()[["period", "value", "yoy_growth", "moving_avg"]].tail(4))
print(f"Wrote dwelling_prices_analysis.csv\nCite: {result.citation}")

section("6. Several series are refused")
mixed = ine.get_data("0004167", {"Dim1": "S7A2025", "Dim3": "T", "Dim4": "T"})
try:
    mixed.calculate_yoy_growth()
except Exception as e:  # one row per district: not one series
    print(f"{type(e).__name__}: {e}")
