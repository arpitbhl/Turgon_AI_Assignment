import json
from pathlib import Path
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
CSV_PATH = BASE_DIR / "Data" / "raw_tickets.csv"
OUTPUT_JSON_PATH = BASE_DIR / "Medallion Pipeline" / "unique_summary.json"

# 1. Load CSV data
df = pd.read_csv(CSV_PATH)
initial_row_count = len(df)

excluded_users = {"test", "system", "admin"}

# Drop null/NaN values in 'submitted_by'
df_cleaned = df.dropna(subset=["submitted_by"]).copy()

# Standardize to lowercase and strip whitespace for comparison
clean_submitted = (
    df_cleaned["submitted_by"].astype(str).str.strip().str.lower()
)

# Keep only rows that are neither blank nor in the exclusion set
valid_mask = (clean_submitted != "") & (~clean_submitted.isin(excluded_users))
df_filtered = df_cleaned[valid_mask]

# Calculate and print removed rows
deleted_rows = initial_row_count - len(df_filtered)
print(f"Total rows deleted: {deleted_rows}")
print(f"Rows remaining: {len(df_filtered)} / {initial_row_count}")

# 2. Specify columns to analyze
target_columns = ["category", "priority", "status", "building", "submitted_by", "assigned_to"]

results = {}

for col in target_columns:
    if col in df_filtered.columns:
        # Clean: drop nulls, convert to string, strip whitespace, and lower
        cleaned = df_filtered[col].dropna().astype(str).str.strip().str.lower()

        results[col] = {
            "unique_count": int(cleaned.nunique()),
            "unique_values": sorted(cleaned.unique().tolist()),
        }

# 3. Save output to a JSON file
with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as f:
    json.dump(results, f, indent=4)

print(f"Saved unique summary to {OUTPUT_JSON_PATH}")