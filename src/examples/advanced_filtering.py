"""Filtering, geographic levels, several codes per dimension, and exports.

Indicators used (verified 2026-11-27):
- 0008273 resident population by NUTS region and municipality (annual, 2011-2023)
- 0012234 median value of dwelling sales (quarterly; Dim3 H1 total, H11 new, H12 existing)
- 0011776 printed edited books by language (annual)

Source: Statistics Portugal (INE), https://www.ine.pt.
"""

import pandas as pd

from pyptine import INE
from pyptine.processors.excel import export_multiple_sheets
from pyptine.utils.exceptions import DimensionError, INERefusalError

ine = INE(language="EN")


def section(title: str) -> None:
    print("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70)


section("1. Several codes per dimension (a list, or a comma-separated string)")
r = ine.get_data("0012234", {"Dim1": "S5A20261", "Dim2": ["PT", "11", "1A"], "Dim3": "H11,H12"})
df = r.to_dataframe()
print(df.pivot(index="geodsg", columns="dim_3_t", values="value"))

section("2. Every geographic level in one request")
r = ine.get_data("0008273", {"Dim1": "S7A2023", "Dim3": "T", "Dim4": "T"})
levels = pd.Series([row["geo_level"] for row in r.with_geo_level().data]).value_counts()
print(levels.to_string())
# Adding all rows would count each person several times; pick one level first.
munis = r.at_level("municipality")
print(f"Sum over municipalities: {sum(x['value'] for x in munis.data):,.0f}")
print(f"Portugal:                {r.at_level('country').data[0]['value']:,.0f}")

section("3. The largest municipalities (by code, never by name)")
top = sorted(munis.data, key=lambda x: -x["value"])[:5]
for row in top:
    print(f"- {row['geocod']} {row['geodsg']}: {row['value']:,.0f}")

section("4. Extra dimensions appear as dim_<n> (code) and dim_<n>_t (label)")
r = ine.get_data("0011776", {"Dim1": "S7A2025"})
print(r.to_dataframe()[["dim_3", "dim_3_t", "value"]].head(6).to_string(index=False))

section("5. Mistakes are refused with the reason")
try:
    ine.get_data("0012234", {"Dim1": "S5A20261", "Dim2": "PT", "Dim3": "H.1"})
except DimensionError as e:  # categ_cod instead of the code INE filters by
    print(f"DimensionError: {e}")
try:
    ine.get_data("0008273", {"Dim1": "2022"})
except DimensionError as e:  # a period label instead of its code
    print(f"DimensionError: {e}")
try:
    ine.get_data("9999999")
except INERefusalError as e:
    print(f"INERefusalError: {e}")

section("6. Exports carry provenance")
sales = ine.get_data("0012234", {"Dim1": ["S5A20254", "S5A20261"], "Dim2": "PT", "Dim3": "H1"})
sales.to_json("dwelling_sales.json")
sales.to_excel("dwelling_sales.xlsx")  # a Data sheet and a Provenance sheet (needs openpyxl)
export_multiple_sheets(
    {"municipalities": munis, "dwelling sales": sales},
    "combined.xlsx",  # one Provenance column per sheet
)
print("Wrote dwelling_sales.json, dwelling_sales.xlsx, combined.xlsx")
print(f"Cite: {sales.citation}")
