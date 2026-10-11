import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("BronzeLayer")

BASE_DIR = Path(__file__).resolve().parent.parent
CSV_PATH = BASE_DIR / "Data" / "raw_tickets.csv"
DB_PATH = BASE_DIR / "data_storage" / "medallion.db"


def compute_row_hash(row_series: pd.Series) -> str:
    row_str = "|".join(row_series.astype(str).fillna("").values)
    return hashlib.sha256(row_str.encode("utf-8")).hexdigest()


def run_bronze() -> int:
    logger.info("Starting Bronze layer ingestion...")

    if not CSV_PATH.exists():
        raise FileNotFoundError(f"Source file not found at: {CSV_PATH}")

    # Read all columns as string for schema-on-read (no loss)
    df_raw = pd.read_csv(CSV_PATH, dtype=str)
    initial_count = len(df_raw)
    logger.info(f"Loaded {initial_count} raw records from {CSV_PATH.name}")

    # Add audit metadata
    now_utc = datetime.now(timezone.utc).isoformat()
    df_raw["_source_file"] = CSV_PATH.name
    df_raw["_ingested_at"] = now_utc
    df_raw["_row_hash"] = df_raw.apply(compute_row_hash, axis=1)

    # Persist idempotently into SQLite
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        df_raw.to_sql("bronze_tickets", conn, if_exists="replace", index=False)
        cursor = conn.cursor()
        count = cursor.execute("SELECT COUNT(*) FROM bronze_tickets").fetchone()[0]

    logger.info(f"Bronze layer complete. Total records stored: {count}")
    return count


if __name__ == "__main__":
    run_bronze()