#!/bin/bash
#
# pyptine CLI Usage Examples
#
# This script demonstrates common CLI commands for interacting with the INE API.
# For more details on any command, run `pyptine [COMMAND] --help`.

echo "=========================================="
echo "pyptine CLI Examples"
echo "=========================================="

# --- Searching for Indicators ---
echo -e "\n# 1. Search for indicators by keyword"
pyptine search "gdp" --limit 5

echo -e "\n# 2. Search for indicators within a specific theme"
pyptine search "employment" --theme "Labour market" --limit 5

# --- Getting Indicator Information ---
echo -e "\n# 3. Get detailed information about an indicator"
pyptine info 0004167

echo -e "\n# 4. Get information in Portuguese"
pyptine info 0004167 --lang PT

# --- Listing Available Metadata ---
echo -e "\n# 5. List all available statistical themes"
pyptine list-commands themes

echo -e "\n# 6. List indicators within a theme"
pyptine list-commands indicators --theme "Population" --limit 5

echo -e "\n# 7. View available dimensions for an indicator"
pyptine dimensions 0004167

# --- Downloading Data ---
# Without Dim1 INE returns only the latest period; without the other filters, every region,
# sex and age group. Codes come from `pyptine dimensions` (Dim1 S7A2025, not 2025).
echo -e "
# 8. Download the latest period to CSV (the header carries the provenance)"
pyptine download 0004167 --output population_latest.csv

echo -e "
# 9. Download one series: Portugal, both sexes, all ages, three years"
pyptine download 0004167 --output population_portugal.csv     -d "Dim1=S7A2023,S7A2024,S7A2025" -d Dim2=PT -d Dim3=T -d Dim4=T

echo -e "
# 10. The same as JSON, and as Excel (a Data and a Provenance sheet; needs openpyxl)"
pyptine download 0004167 -f json --output population_portugal.json -d Dim1=S7A2025 -d Dim2=PT
pyptine download 0004167 -f xlsx --output population_portugal.xlsx -d Dim1=S7A2025 -d Dim2=PT

# --- Cache Management ---
echo -e "\n# 11. View cache information"
pyptine cache info

echo -e "\n# 12. Clear the cache (requires confirmation)"
echo "# To run: pyptine cache clear --yes"
echo "# (Command is commented out to prevent accidental clearing)"

# --- Help and Version ---
echo -e "\n# 13. Get help for a specific command"
pyptine download --help

echo -e "\n# 14. Check the installed version of pyptine"
pyptine --version

echo -e "\n=========================================="
echo "All CLI examples demonstrated!"
echo "=========================================="