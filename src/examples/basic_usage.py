"""Basic usage of pyptine.

Indicators used (all verified against INE's catalogue, 2026-11-27):
- 0004167 resident population by district/region, sex and age group (annual, 1991-2025)
- 0008273 resident population by NUTS region and municipality (annual, 2011-2023)

Source: Statistics Portugal (INE), https://www.ine.pt. Reuse is permitted on condition that
the source is cited; every file written below carries the citation.
"""

from pyptine import INE

ine = INE(language="EN")  # caching is on by default


def section(title: str) -> None:
    print("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70)


section("1. Search the catalogue")
results = ine.search("resident population")
print(f"{len(results)} indicators match 'resident population'; the first five:")
for indicator in results[:5]:
    print(f"- {indicator.varcd}: {indicator.title} (theme: {indicator.theme})")

section("2. Metadata of one indicator")
varcd = "0004167"
metadata = ine.get_metadata(varcd)  # also fills theme, source, ... from the catalogue
print(f"{metadata.varcd}: {metadata.title}")
print(f"- unit: {metadata.unit}; periodicity: {metadata.periodicity}")
print(f"- theme: {metadata.theme} / {metadata.subtheme}")
print(f"- source: {metadata.source}")

section("3. Dimensions and their codes")
# Filters take the codes (DimensionValue.code), never the labels: Dim1 "S7A2023", not "2023".
for dim in ine.get_dimensions(varcd):
    sample = ", ".join(f"{v.code}={v.label}" for v in dim.values[:4])
    print(f"Dim{dim.id} {dim.name}: {len(dim.values)} values ({sample}, ...)")

section("4. One figure: Portugal, both sexes, all ages, 2025")
# Without Dim1 INE returns only the latest period; without the other filters, every region,
# sex and age group. Pin each dimension to get one series.
total = {"Dim2": "PT", "Dim3": "T", "Dim4": "T"}
r = ine.get_data(varcd, {"Dim1": "S7A2025", **total})
print(r.to_dataframe()[["period", "period_code", "geodsg", "value"]])

section("5. Several periods in one request")
r = ine.get_data(varcd, {"Dim1": ["S7A2015", "S7A2020", "S7A2025"], **total})
for row in r.data:
    print(f"{row['period']}: {row['value']:,.0f}")

section("6. Geographic levels")
# One request returns every level at once; the level comes from the codes. In 0004167 a
# two-digit code is a district (in NUTS indicators it is a NUTS II region).
r = ine.get_data(varcd, {"Dim1": "S7A2025", "Dim3": "T", "Dim4": "T"})
districts = r.at_level("district")
print(f"{len(districts.data)} districts; the three largest:")
for row in sorted(districts.data, key=lambda x: -x["value"])[:3]:
    print(f"- {row['geodsg']}: {row['value']:,.0f}")

munis = ine.get_data("0008273", {"Dim1": "S7A2023", "Dim3": "T", "Dim4": "T"})
print(f"0008273 in 2023: {len(munis.at_level('municipality').data)} municipalities")

section("7. Export with provenance")
r = ine.get_data(varcd, {"Dim1": ["S7A2024", "S7A2025"], **total})
r.to_csv("population_portugal.csv")  # the header carries source, licence, URL, INE's date
print("Wrote population_portugal.csv")
print(f"Cite: {r.citation}")

section("8. Themes")
themes = ine.list_themes()
print(f"{len(themes)} themes, e.g. {', '.join(themes[:5])}")
labour = ine.search(query="", theme="Labour market")
print(f"{len(labour)} indicators in 'Labour market', e.g. {labour[0].varcd}: {labour[0].title}")

section("9. Validate codes")
for code in (varcd, "9999999"):
    print(f"{code}: {'valid' if ine.validate_indicator(code) else 'not in the catalogue'}")
