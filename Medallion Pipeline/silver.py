import logging
from pathlib import Path
import re
import sqlite3
from datetime import datetime, timezone
import pandas as pd

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("SilverLayer")

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data_storage" / "medallion.db"

EXCLUDED_USERS = {"test", "system", "admin"}

PRIORITY_MAP = {
    "crit": "critical",
    "critical": "critical",
    "hi": "high",
    "high": "high",
    "med": "medium",
    "medium": "medium",
    "lo": "low",
    "low": "low",
}

NAME_MAP = {
    "a. garcia": "alex garcia",
    "alex garcia": "alex garcia",
    "bob martinez": "robert martinez",
    "r. martinez": "robert martinez",
    "robert martinez": "robert martinez",
    "c. taylor": "chris taylor",
    "chris taylor": "chris taylor",
    "d. kim": "david kim",
    "david kim": "david kim",
    "e. brown": "emily brown",
    "emily brown": "emily brown",
    "j. doe": "jane doe",
    "jane doe": "jane doe",
    "j. smith": "john smith",
    "john smith": "john smith",
    "jordan rivera": "jordan rivera",
    "k. lee": "karen lee",
    "karen lee": "karen lee",
    "l. chen": "lisa chen",
    "lisa chen": "lisa chen",
    "m. johnson": "michael johnson",
    "mike johnson": "michael johnson",
    "michael johnson": "michael johnson",
    "nancy white": "nancy white",
    "pat anderson": "pat anderson",
    "s. williams": "sarah williams",
    "sarah williams": "sarah williams",
    "sam jackson": "sam jackson",
    "t. wilson": "tom wilson",
    "tom wilson": "tom wilson",
}

# Regex matching: "Duplicate of ticket #10623", "ticket #10623", "#10623", "TKT-10623"
DUPLICATE_REGEX = re.compile(
    r"(?:duplicate|dup|merged|see|ref)?\s*(?:of)?\s*(?:ticket)?\s*#?\s*(\d{3,6})\b|\bTKT-(\d{3,6})\b",
    re.IGNORECASE,
)


def extract_referenced_parent(notes: str, self_id: str, id_has_tkt_prefix: bool) -> str | None:
    """Extracts referenced parent ticket ID from resolution notes."""
    if not notes or pd.isna(notes):
        return None
    text = str(notes).strip()
    self_clean = str(self_id).strip().upper()

    matches = DUPLICATE_REGEX.findall(text)
    for m in matches:
        num = m[0] if m[0] else m[1]
        if not num:
            continue
        candidate = f"TKT-{num}" if id_has_tkt_prefix else num
        # Prevent self reference
        if candidate.upper() != self_clean and num != self_clean.replace("TKT-", ""):
            return candidate
    return None


def normalize_category(raw_val: str) -> str:
    """Classifies messy raw category inputs into standard business domains."""
    if not raw_val or str(raw_val).strip().lower() in ["unknown", "none", "nan", ""]:
        return "Miscellaneous"

    val = str(raw_val).lower().strip()

    if any(k in val for k in ["ac", "a/c", "air condition", "air cond", "cooling", "chiller"]):
        return "HVAC - Air Conditioning"
    if any(k in val for k in ["heat", "boiler", "furnace", "radiator"]):
        return "HVAC - Heating"
    if any(k in val for k in ["vent", "exhaust", "airflow", "duct"]):
        return "HVAC - Ventilation"
    if "hvac" in val:
        return "HVAC - Others"

    if "elevator" in val or "lift" in val:
        return "Electrical System - Elevator"
    if "escalator" in val:
        return "Electrical Systems - Escalator"
    if any(k in val for k in ["blackout", "power outage", "outage", "generator", "surge", "power"]):
        return "Power Issue"
    if any(k in val for k in ["electric", "wiring", "socket", "outlet", "breaker", "lighting", "bulb"]):
        return "Electrical System - Others"

    if any(k in val for k in ["plumb", "pipe", "leak", "faucet", "drain", "clog", "sewage", "sink", "toilet"]):
        return "Plumbing"
    if any(k in val for k in ["fire", "alarm", "extinguisher", "smoke", "sprinkler", "evac"]):
        return "Fire/Safety"
    if any(k in val for k in ["pest", "bug", "roach", "rodent", "rat", "mice", "termite", "insect"]):
        return "Pest Control"
    if any(k in val for k in ["housekeep", "clean", "trash", "janitor", "spill", "sanit"]):
        return "HouseKeeping"
    if any(k in val for k in ["it", "wifi", "internet", "network", "computer", "server", "printer", "laptop"]):
        return "IT Support"
    if any(k in val for k in ["maint", "repair", "fixture", "door", "lock", "window", "furniture"]):
        return "Maintenance"

    return "Miscellaneous"


def clean_numeric_field(series: pd.Series) -> pd.Series:
    """Strips non-numeric characters, sets TBD/empty to -1.0, and casts to float."""
    def _parse(val):
        if pd.isna(val):
            return -1.0
        val_str = str(val).strip().lower()
        if val_str in ["tbd", "none", "null", "nan", ""]:
            return -1.0
        cleaned = re.sub(r"[^\d.-]", "", val_str)
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return -1.0
    return series.apply(_parse)


def run_silver() -> int:
    logger.info("Starting Silver layer cleansing & bidirectional reconciliation...")

    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query("SELECT * FROM bronze_tickets", conn)

    initial_rows = len(df)
    logger.info(f"Read {initial_rows} rows from bronze_tickets")

    # 1. Deduplication using row hash and ticket_id
    df = df.drop_duplicates(subset=["_row_hash"]).copy()
    if "ticket_id" in df.columns:
        df = df.drop_duplicates(subset=["ticket_id"], keep="last").copy()
    logger.info(f"Deduplication removed {initial_rows - len(df)} duplicates")

    # 2. Filter invalid & automated users
    before_filter = len(df)
    if "submitted_by" in df.columns:
        clean_submitted = df["submitted_by"].fillna("").astype(str).str.strip().str.lower()
        valid_mask = (clean_submitted != "") & (~clean_submitted.isin(EXCLUDED_USERS))
        df = df[valid_mask].copy()
    logger.info(f"Filtered {before_filter - len(df)} system/blank submitter rows")

    # CRITICAL FIX: Reset index so row labels align continuously from 0 to N-1
    df = df.reset_index(drop=True)

    # 3. Canonical Name Resolution
    if "assigned_to" in df.columns:
        df["assigned_to"] = df["assigned_to"].fillna("unknown").astype(str).str.strip().str.lower().map(lambda x: NAME_MAP.get(x, x))
    if "submitted_by" in df.columns:
        df["submitted_by"] = df["submitted_by"].fillna("unknown").astype(str).str.strip().str.lower().map(lambda x: NAME_MAP.get(x, x))

    # 4. Standardize Priority Mapping
    if "priority" in df.columns:
        df["priority"] = df["priority"].fillna("medium").astype(str).str.strip().str.lower().map(lambda x: PRIORITY_MAP.get(x, "medium"))

    # 5. Clean Free-text Description & Resolution Notes
    df["description"] = df["description"].fillna("").astype(str).str.strip() if "description" in df.columns else ""
    df["resolution_notes"] = df["resolution_notes"].fillna("").astype(str).str.strip() if "resolution_notes" in df.columns else ""

    # 6. Apply Categorization Mapping
    if "category" in df.columns:
        raw_categories = df["category"].fillna("unknown").astype(str).str.strip()
        standardized_categories = raw_categories.apply(normalize_category)
        misc_mask = standardized_categories == "Miscellaneous"

        def append_misc_note(desc: str, raw_cat: str) -> str:
            suffix = f"Category_description: {raw_cat}"
            return f"{desc} | {suffix}" if desc else suffix

        df.loc[misc_mask, "description"] = [
            append_misc_note(d, c)
            for d, c in zip(df.loc[misc_mask, "description"], raw_categories[misc_mask])
        ]
        df["category"] = standardized_categories

    # 7. Categorical normalization
    for col in ["status", "building"]:
        if col in df.columns:
            df[col] = df[col].fillna("unknown").astype(str).str.strip().str.lower().replace({"": "unknown"})

    # 8. Standardize Multi-Format Timestamps to 'YYYY-MM-DD HH:MM:SS'
    for date_col in ["created_at", "resolved_at"]:
        if date_col in df.columns:
            parsed = pd.to_datetime(df[date_col], format="mixed", errors="coerce")
            df[date_col] = parsed.dt.strftime("%Y-%m-%d %H:%M:%S")

    # 9. Clean Numeric Fields (Cost & SLA Hours)
    if "cost" in df.columns:
        df["cost"] = clean_numeric_field(df["cost"])
    if "sla_hours" in df.columns:
        df["sla_hours"] = clean_numeric_field(df["sla_hours"])

    # 10. BIDIRECTIONAL RECONCILIATION VIA RESOLUTION NOTES
    sample_id = str(df["ticket_id"].dropna().iloc[0]) if not df["ticket_id"].empty else ""
    id_has_tkt = sample_id.upper().startswith("TKT-")

    df["parent_ticket_id"] = [
        extract_referenced_parent(notes, tid, id_has_tkt)
        for notes, tid in zip(df["resolution_notes"], df["ticket_id"])
    ]
    df["is_duplicate"] = df["parent_ticket_id"].notna().astype(int)
    df["duplicate_verified"] = 0

    # Build map using the true DataFrame index
    ticket_row_map = {tid: idx for idx, tid in zip(df.index, df["ticket_id"])}
    verified_count = 0
    parent_updates_count = 0

    for idx, row in df[df["is_duplicate"] == 1].iterrows():
        p_id = row["parent_ticket_id"]
        # Normalize parent ID format
        actual_parent = p_id if p_id in ticket_row_map else (
            p_id.replace("TKT-", "") if p_id.startswith("TKT-") else f"TKT-{p_id}"
        )

        if actual_parent in ticket_row_map:
            p_idx = ticket_row_map[actual_parent]
            df.at[idx, "parent_ticket_id"] = actual_parent
            df.at[idx, "duplicate_verified"] = 1
            verified_count += 1

            # --- A. INHERIT FROM PARENT TO CHILD ---
            if df.at[idx, "category"] in ["Miscellaneous", "unknown"] and df.at[p_idx, "category"] not in ["Miscellaneous", "unknown"]:
                df.at[idx, "category"] = df.at[p_idx, "category"]

            if df.at[idx, "assigned_to"] in ["unknown", ""] and df.at[p_idx, "assigned_to"] not in ["unknown", ""]:
                df.at[idx, "assigned_to"] = df.at[p_idx, "assigned_to"]

            if df.at[idx, "cost"] <= 0 and df.at[p_idx, "cost"] > 0:
                df.at[idx, "cost"] = df.at[p_idx, "cost"]

            if df.at[idx, "sla_hours"] <= 0 and df.at[p_idx, "sla_hours"] > 0:
                df.at[idx, "sla_hours"] = df.at[p_idx, "sla_hours"]

            # --- B. BIDIRECTIONAL: REFLECT FROM CHILD TO PARENT ---
            parent_changed = False
            if df.at[p_idx, "cost"] <= 0 and df.at[idx, "cost"] > 0:
                df.at[p_idx, "cost"] = df.at[idx, "cost"]
                parent_changed = True

            if df.at[p_idx, "sla_hours"] <= 0 and df.at[idx, "sla_hours"] > 0:
                df.at[p_idx, "sla_hours"] = df.at[idx, "sla_hours"]
                parent_changed = True

            if df.at[p_idx, "assigned_to"] in ["unknown", ""] and df.at[idx, "assigned_to"] not in ["unknown", ""]:
                df.at[p_idx, "assigned_to"] = df.at[idx, "assigned_to"]
                parent_changed = True

            if df.at[p_idx, "category"] in ["Miscellaneous", "unknown"] and df.at[idx, "category"] not in ["Miscellaneous", "unknown"]:
                df.at[p_idx, "category"] = df.at[idx, "category"]
                parent_changed = True

            if parent_changed:
                parent_updates_count += 1

    logger.info(
        f"Reconciliation: {verified_count} verified duplicates. "
        f"Bidirectionally backfilled {parent_updates_count} parent records from child info."
    )

    # 11. Silver Lineage Metadata
    df["_silver_processed_at"] = datetime.now(timezone.utc).isoformat()

    # 12. Persist to SQLite
    with sqlite3.connect(DB_PATH) as conn:
        df.to_sql("silver_tickets", conn, if_exists="replace", index=False)
        final_count = conn.cursor().execute("SELECT COUNT(*) FROM silver_tickets").fetchone()[0]

    logger.info(f"Silver layer complete. Retained {final_count} high-quality rows")
    return final_count

if __name__ == "__main__":
    run_silver()