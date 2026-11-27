"""Charts with plotly, straight from a DataResponse.

Plots order rows by period code (chronological), label the x axis with INE's period labels,
and draw one line per category with ``color_column``. Each chart is written as an HTML file.

Indicators used (verified 2026-11-27):
- 0004167 resident population by district (annual, 1991-2025)
- 0012234 median value of dwelling sales (quarterly; H11 new, H12 existing)

Source: Statistics Portugal (INE), https://www.ine.pt.
"""

from typing import Any

from pyptine import INE

ine = INE(language="EN")


def periods(varcd: str) -> list[str]:
    return [v.code for v in ine.get_dimensions(varcd)[0].values]


def save(fig: Any, name: str, citation: str) -> None:
    # Cite INE on the chart itself: reuse is conditional on citing the source.
    fig.add_annotation(
        text=citation,
        xref="paper",
        yref="paper",
        x=0,
        y=-0.18,
        showarrow=False,
        font={"size": 10},
        align="left",
    )
    fig.write_html(name)
    print(f"Wrote {name}")


# 1. One series: Portugal's population, every year.
pop = ine.get_data("0004167", {"Dim1": periods("0004167"), "Dim2": "PT", "Dim3": "T", "Dim4": "T"})
save(
    pop.plot_line(title="Resident population, Portugal", markers=True),
    "population.html",
    pop.citation,
)

# 2. Several series: one line per category (new and existing dwellings).
sales = ine.get_data("0012234", {"Dim1": periods("0012234"), "Dim2": "PT", "Dim3": ["H11", "H12"]})
save(
    sales.plot_line(color_column="dim_3_t", title="Median value of dwelling sales, Portugal"),
    "dwelling_prices.html",
    sales.citation,
)

# 3. A bar chart across places: the 18 districts in the latest year.
latest = periods("0004167")[-1]
districts = ine.get_data("0004167", {"Dim1": latest, "Dim3": "T", "Dim4": "T"}).at_level("district")
save(
    districts.plot_bar(x_column="geodsg", title=f"Population by district, {latest[-4:]}"),
    "districts.html",
    districts.citation,
)

# 4. Analysis, then a chart: year-over-year growth.
growth = pop.calculate_yoy_growth()
save(
    growth.plot_bar(y_column="yoy_growth", title="Population, year-over-year growth (%)"),
    "population_growth.html",
    growth.citation,
)

# 5. The other chart types through the generic method.
for kind in ("area", "scatter"):
    save(
        pop.plot(chart_type=kind, title=f"Resident population ({kind})"),
        f"population_{kind}.html",
        pop.citation,
    )
