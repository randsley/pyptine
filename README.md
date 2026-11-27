# pyptine - INE Portugal API Client

[![PyPI version](https://img.shields.io/pypi/v/pyptine.svg)](https://pypi.org/project/pyptine/)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![DOI](https://zenodo.org/badge/1133698090.svg)](https://doi.org/10.5281/zenodo.19118875)

High-level Python client for Statistics Portugal (INE) API. Query and download statistical data from [INE Portugal](https://www.ine.pt) with a simple, intuitive interface.

## Features

- 🎯 **High-level Convenience API**: Simple interface for common data retrieval and analysis tasks.
- ⚡ **Async Support**: Non-blocking I/O with `AsyncINE` for concurrent requests using httpx.
- 📊 **Multiple Output Formats**: Export data to pandas DataFrames, JSON, or CSV with ease.
- 📈 **Data Visualization**: Interactive plotly charts (line, bar, area, scatter) directly from data.
- 🔬 **Statistical Analysis**: Built-in YoY growth, MoM changes, moving averages, and EMA calculations.
- 💾 **Smart Caching**: Disk-based caching reduces redundant API calls, speeding up repeated queries.
- 🔍 **Metadata Browsing**: Search and discover indicators, themes, and dimensions.
- 🖥️ **Enhanced CLI**: Rich formatting with progress bars, tables, and colored output.
- 📅 **Every Period**: `get_all_data()` fetches all of an indicator's periods, a batch per request.
- 📖 **Modern Python**: Fully type-annotated for better developer experience and IDE support.
- ✅ **Tested against real INE responses**: offline tests on recorded responses, plus a weekly check against the live API.
- 🛑 **Refuses rather than guesses**: INE's refusals and invalid dimension codes raise errors that name the cause.

## Installation

```bash
pip install pyptine
```

For development, install with all extra dependencies:

```bash
pip install "pyptine[dev,docs]"
```

## Quick Start

```python
from pyptine import INE

# Initialize the client
ine = INE(language="EN")

# 1. Search for an indicator
print("Searching for 'gdp' indicators...")
results = ine.search("gdp")
for indicator in results[:5]:  # Print top 5 results
    print(f"- {indicator.varcd}: {indicator.title}")

# 2. Get data for a specific indicator (without a period filter: the latest period only)
varcd = "0008273"  # Resident population by place of residence, sex and age group
print(f"\nFetching data for indicator {varcd}...")
response = ine.get_data(varcd, dimensions={"Dim1": ["S7A2022", "S7A2023"], "Dim2": "PT"})

# 3. Convert to a pandas DataFrame
df = response.to_dataframe()
print("\nData as DataFrame:")
print(df.head())

# 4. Export data to a CSV file
output_file = "population_data.csv"
print(f"\nExporting data to {output_file}...")
ine.export_csv(varcd, output_file)
print("Done!")
```

## Async API

For concurrent requests and non-blocking I/O, use the `AsyncINE` client:

```python
import asyncio
from pyptine import AsyncINE

async def main():
    async with AsyncINE(language="EN") as ine:
        # Fetch single indicator
        response = await ine.get_data("0004167")
        df = response.to_dataframe()
        print(df.head())

        # Fetch multiple indicators concurrently
        import asyncio
        responses = await asyncio.gather(
            ine.get_data("0004167"),
            ine.get_data("0008273"),
            ine.get_data("0008074")
        )

        # Every period, a batch of periods per request
        async for chunk in ine.get_all_data("0008273", {"Dim2": "PT"}, periods_per_request=5):
            df_chunk = chunk.to_dataframe()
            print(f"Processing {len(df_chunk)} rows...")

asyncio.run(main())
```

**AsyncINE Features:**
- Non-blocking I/O for faster concurrent requests
- Async iterator over batches of periods (`get_all_data`)
- Same parsing, validation and errors as the synchronous `INE` client
- The same retries and timeouts as `INE` (no cache)

## Command-Line Usage

The pyptine CLI provides a convenient way to access INE data from your terminal, with rich formatting and progress indicators for a better user experience.

```bash
# Search for indicators related to "pib" (GDP in Portuguese)
pyptine search "pib"

# Get detailed information about a specific indicator
pyptine info 0008273

# Download data for an indicator to a CSV file (with progress bar)
pyptine download 0008273 --output data.csv

# Download data and filter by dimensions
pyptine download 0008273 --output filtered_data.csv -d Dim1=S7A2022,S7A2023 -d Dim2=PT

# List all available statistical themes (in formatted table)
pyptine list-commands themes

# List all indicators (with pagination support)
pyptine list-commands indicators --limit 50

# View available dimensions for an indicator
pyptine dimensions 0004167

# Clear the local cache
pyptine cache clear
```

**CLI Features:**
- **Rich Formatting** - Tables, panels, and colored output for better readability
- **Progress Indicators** - Spinners and progress bars for long-running operations
- **Error Handling** - Centralized, user-friendly error messages with context
- **Better Organization** - Data displayed in well-formatted tables rather than plain text

## Documentation

### Initializing the Client

The `INE` class is the main entry point.

```python
from pyptine import INE
from pathlib import Path

# Default client (language='EN', caching=True)
ine = INE()

# Client with Portuguese language
ine_pt = INE(language="PT")

# Disable caching
ine_no_cache = INE(cache=False)

# Use a custom cache directory
ine_custom_cache = INE(cache_dir=Path("/path/to/custom/cache"))

# A different read timeout (default 130 s)
ine_patient = INE(timeout=300)
```

**Retries and timeouts.** INE answers HTTP 404 for valid codes when it throttles a burst of
requests, resets connections and answers 429/5xx under load, and its gateway gives up with a
504 after 120 s. pyptine retries those (and failed connections and malformed bodies) after 5,
20 and 60 s, honouring a 429's `Retry-After` up to 120 s. The default read timeout (130 s) is
longer than INE's gateway limit, so a slow request ends in a retried 504. Not retried: a read
timeout (filter more dimensions or fewer periods, or raise `timeout`), other HTTP errors, and
INE's refusals (`INERefusalError`). Each retry is logged as a warning.

### Working with Indicators

#### Searching for Indicators

You can search for indicators by keyword and filter by theme or sub-theme.

```python
# Basic search
results = ine.search("unemployment rate")

# Search within a specific theme
results = ine.search("employment", theme="Labour market")
```

#### Getting Indicator Metadata

Retrieve detailed information about an indicator, including its dimensions.

```python
metadata = ine.get_metadata("0008273")
print(f"Title: {metadata.title}")
print(f"Unit: {metadata.unit}")
print(f"Theme: {metadata.theme}")
print(f"Source: {metadata.source}")

# List available dimensions
dimensions = ine.get_dimensions("0008273")
for dim in dimensions:
    print(f"\nDimension: {dim.name}")
    for value in dim.values[:5]:  # Show first 5 values
        print(f"- {value.code}: {value.label}")
```

### Fetching and Exporting Data

#### Getting Data

The `get_data` method returns a `DataResponse` object, which can be easily converted to different formats.
Every row carries `period` (INE's label, e.g. `"2022"` or `"1st Quarter 2026"`), `period_code`
(e.g. `"S7A2022"`), `geocod`/`geodsg` (geographic code and name), any extra dimensions as
`dim_<n>`/`dim_<n>_t` (code and label), `ind_string` (INE's display form) and `value` (a float,
or None where INE suppresses the figure).

```python
response = ine.get_data("0008273")  # the latest period only

# Convert to pandas DataFrame
df = response.to_dataframe()

# Convert to a dictionary
data_dict = response.to_dict()

# Get data as a JSON string
json_str = response.to_json()
```

#### Filtering Data with Dimensions

Use the `dimensions` parameter to filter data before downloading. `Dim1` is always the period
and `Dim2` the geography. Values are **codes**, the `code` of each value from `get_dimensions()`
(INE's `cat_id`): the period `S7A2023`, not its label `2023`. A value may be one code, a list
of codes or a comma-separated string.

```python
filtered_response = ine.get_data(
    "0008273",
    dimensions={
        "Dim1": ["S7A2022", "S7A2023"],  # two years
        "Dim2": "PT",                    # Portugal
        "Dim3": "T",                     # both sexes
        "Dim4": "T",                     # all ages
    }
)
df_filtered = filtered_response.to_dataframe()
```

Things INE does that are worth knowing (verified 2026-11-27):

- **Without `Dim1`, INE returns only the latest period.** Ask for periods explicitly, or use `get_all_data()`.
- **One request returns every geographic level at once** (country, NUTS I/II/III, municipalities and parishes for many indicators), and some indicators add rows that are not places (residence abroad, unknown, the grand total). Filter `Dim2`, or the rows by level, before adding values up:

  ```python
  r = ine.get_data("0008273", {"Dim1": "S7A2022", "Dim3": "T", "Dim4": "T"})
  r.with_geo_level()            # rows gain geo_level: country, nuts1, nuts2, nuts3,
                                # district, municipality, parish, or abroad / unknown / total
  r.at_level("municipality")    # the 308 municipalities only
  ```

  The level comes from the code (`geocod`), never the name: INE's English names are
  inconsistent ("Continente" stays Portuguese; abroad is "Foreigner"). Some indicators
  (0004167) use districts instead of NUTS, and there a two-digit code is a district (`11` is
  Lisboa's district, not the NUTS II region Norte): the classification comes from the
  indicator's metadata (`DataResponse.geo_classification`). Codes INE is not known to issue
  are refused. See `pyptine.geography` (`geo_level`, `pseudo_unit`, `classify_geography`).
- **An invalid code raises `DimensionError`** before any request, naming the code to use when you passed a label or INE's `categ_cod` (e.g. `H.1` for `H1`).
- **INE's refusals raise `INERefusalError`** (INE answers them with HTTP 200), with INE's `ine_code` and `ine_message`. They are never cached.

#### Exporting Data

You can export data directly to CSV or JSON files.

```python
# Export to CSV
ine.export_csv("0008273", "output.csv")

# Export to JSON with pretty printing
ine.export_json("0008273", "output.json", pretty=True)

# Export to Excel (pip install pyptine[excel]): a Data sheet and a Provenance sheet
ine.export_excel("0008273", "output.xlsx", dimensions={"Dim2": "PT"})

# Export filtered data
ine.export_csv(
    "0008273",
    "filtered_output.csv",
    dimensions={"Dim1": "S7A2023", "Dim2": "PT"}
)
```

**Provenance.** Every `DataResponse` records where its data came from, and the CSV header
(`include_metadata=True`, the default), the Excel file's `Provenance` sheet and the JSON
file write it out: source, INE's licence
terms, the citation INE requires, indicator, title, unit, language, periods (labels and
codes), the dimension filters sent, the request URL, INE's metadata page, INE's extraction
timestamp (`DataExtracao`; a response served from the cache keeps INE's date) and the date of
INE's last update. Reuse of INE data, commercial use included, is permitted under EU
Directive 2019/1024 on condition that the source is cited:

```python
from pyptine.provenance import ine_citation

r = ine.get_data("0008273", {"Dim1": ["S7A2021", "S7A2022"], "Dim2": "PT", "Dim3": "T", "Dim4": "T"})
r.citation
# 'Source: Statistics Portugal (INE), indicator 0008273, reference periods 2021, 2022, extracted 2026-11-27.'
ine_citation(r, other_response)  # one sentence for several extracts
```

`pyptine.processors.export_multiple_sheets({"name": response, ...}, path)` writes several
responses to one workbook, with one Provenance column per sheet; a plain DataFrame carries no
provenance, so pass `metadata={...}` to describe it. The catalogue's
`CatalogueResponse.extraction_date` is INE's `<extraction_date>`; INE states no timezone for it,
so it is left naive.

#### Every Period of an Indicator

INE has no row pagination, and returns only the latest period when no period is given.
`get_all_data()` asks for the indicator's periods (or those in `dimensions["Dim1"]`), a batch
of `periods_per_request` periods per request, oldest first:

```python
import pandas as pd

chunks = ine.get_all_data("0008273", {"Dim2": "PT", "Dim3": "T", "Dim4": "T"})
df = pd.concat([chunk.to_dataframe() for chunk in chunks], ignore_index=True)

# Fewer periods per request for indicators with many rows per period
for chunk in ine.get_all_data("0008273", periods_per_request=1):
    print(chunk.data[0]["period"], len(chunk.data), "rows")
```

`chunk_size` (from earlier versions) is ignored, with a `DeprecationWarning`.

#### Visualizing Data

Create interactive visualizations directly from indicator data without exporting to DataFrame:

```python
# Get data and create interactive line chart (x axis: the period label, in period order)
response = ine.get_data("0008273", {"Dim1": ["S7A2021", "S7A2022", "S7A2023"],
                                    "Dim2": "PT", "Dim3": "T", "Dim4": "T"})
fig = response.plot(chart_type="line")
fig.show()

# Different chart types
fig_bar = response.plot_bar()
fig_area = response.plot_area()
fig_scatter = response.plot_scatter()

# Customize visualization
fig = response.plot_line(
    markers=True,
    x_column="period",
    y_column="value"
)

# Color by a dimension (one line per geographic unit, sex, ...)
fig = response.plot_line(color_column="geodsg")

# Save to HTML for sharing
fig.write_html("indicator_plot.html")

# Further customization with plotly
fig.update_layout(height=600, width=1200, title="Custom Title")
fig.show()
```

**Available Visualization Methods:**
- `plot(chart_type)` - Generic plot with selectable chart type
- `plot_line()` - Interactive line chart with optional markers
- `plot_bar()` - Bar chart for categorical comparison
- `plot_area()` - Stacked area chart for trends
- `plot_scatter()` - Scatter plot with optional size and color dimensions

All methods support:
- Interactive plotly charts with hover, zoom, and pan
- Custom column selection for x/y axes
- Color coding by dimension columns
- Export to HTML, PNG, or other formats

#### Advanced Data Analysis

Perform statistical calculations on one series (one row per period). INE data usually holds
several series per period (every geographic unit, every category), so filter to one first; the
methods refuse data with several rows per period. Periods are ordered by `period_code`.

```python
# One series: Portugal, both sexes, all ages, three years
response = ine.get_data("0008273", {"Dim1": ["S7A2021", "S7A2022", "S7A2023"],
                                    "Dim2": "PT", "Dim3": "T", "Dim4": "T"})
yoy_response = response.calculate_yoy_growth()
df_yoy = yoy_response.to_dataframe()
print(df_yoy[['period', 'value', 'yoy_growth']])

# Calculate month-over-month changes
mom_response = response.calculate_mom_change()
df_mom = mom_response.to_dataframe()

# Calculate simple moving average (3-period)
ma_response = response.calculate_moving_average(window=3)
df_ma = ma_response.to_dataframe()

# Calculate exponential moving average
ema_response = response.calculate_exponential_moving_average(span=5)
df_ema = ema_response.to_dataframe()

# Chain multiple analyses
result = response.calculate_yoy_growth().calculate_moving_average(window=2)
df = result.to_dataframe()
print(df[['period', 'value', 'yoy_growth', 'moving_avg']])
```

Available analysis methods on `DataResponse`:
- `calculate_yoy_growth()` - Percentage change from the same period one year earlier (same quarter, same month)
- `calculate_mom_change()` - Percentage change from the preceding period
- `calculate_moving_average(window)` - Simple moving average
- `calculate_exponential_moving_average(span)` - Exponential weighted moving average

All methods support custom `value_column` and `period_column` parameters to work with different data structures.

## API Reference

### `INE` Class

The main class for interacting with the INE API.

`INE(language: str = "EN", cache: bool = True, cache_dir: Optional[Path] = None, cache_ttl: Optional[int] = None, timeout: int = 130)`. `cache_ttl` is how long data responses stay cached (default 1 day); metadata and the catalogue stay 7 days.

| Method | Description |
| --- | --- |
| `search(query, ...)` | Search for indicators. |
| `get_data(varcd, ...)` | Get data for an indicator as a `DataResponse` object (the latest period unless `Dim1` is given). |
| `get_all_data(varcd, ...)` | Get every period of an indicator, one `DataResponse` per batch of periods. |
| `get_metadata(varcd, catalogue=True)` | Metadata for an indicator: dimensions, unit, periodicity and dates from INE's metadata endpoint; description, theme, subtheme, keywords, source, geographic level and URLs from the catalogue (one more request, cached; `catalogue=False` leaves them None). |
| `get_dimensions(varcd)` | Get available dimensions for an indicator. |
| `get_indicator(varcd)` | Get catalogue information for a single indicator. |
| `validate_indicator(varcd)` | Check if an indicator code is valid. |
| `list_themes()` | Get a list of all available themes. |
| `export_csv(varcd, ...)` | Export indicator data to a CSV file. |
| `export_json(varcd, ...)` | Export indicator data to a JSON file. |
| `clear_cache()` | Clear all cached data. |
| `get_cache_info()` | Get statistics about the cache. |

---

## Links & Resources

- **PyPI Package**: https://pypi.org/project/pyptine/
- **GitHub Repository**: https://github.com/randsley/pyptine
- **Issue Tracker**: https://github.com/randsley/pyptine/issues
- **INE Portal**: https://www.ine.pt/

---

## Development

### Setup

To set up the development environment:

```bash
# Clone the repository
git clone https://github.com/nigelrandsley/pyptine.git
cd pyptine

# Install in editable mode with development dependencies
pip install -e ".[dev]"

# Install pre-commit hooks to ensure code quality
pre-commit install
```

### Running Tests

```bash
# Run all tests
pytest

# Run tests with coverage report
pytest --cov=src/pyptine --cov-report=term-missing
```

### Code Quality

This project uses `black` for formatting, `ruff` for linting, and `mypy` for type checking.

```bash
# Format code
black src/ tests/

# Lint code
ruff check src/ tests/

# Type check
mypy src/
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1.  Fork the repository.
2.  Create your feature branch (`git checkout -b feature/amazing-feature`).
3.  Commit your changes (`git commit -m 'Add amazing feature'`).
4.  Push to the branch (`git push origin feature/amazing-feature`).
5.  Open a Pull Request.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.