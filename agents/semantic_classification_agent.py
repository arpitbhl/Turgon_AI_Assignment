import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3
import pandas as pd
from .llm_client import LLMClient

logger = logging.getLogger("SemanticClassificationAgent")
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data_storage" / "medallion.db"


class SemanticClassificationAgent:

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.llm = LLMClient()
        self._init_cache_tables()

    def _init_cache_tables(self):
        with sqlite3.connect(self.db_path) as conn:
            # 1. Semantic classification cache
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS silver_semantic_cache (
                    text_hash VARCHAR PRIMARY KEY,
                    raw_text TEXT,
                    inferred_domain VARCHAR,
                    sub_category VARCHAR,
                    root_cause_hint VARCHAR,
                    sentiment_urgency VARCHAR,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """
            )
            # 2. Duplicate semantic verification audit log
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS silver_duplicate_llm_verification (
                    child_ticket_id VARCHAR PRIMARY KEY,
                    parent_ticket_id VARCHAR,
                    llm_verdict VARCHAR,
                    confidence_score FLOAT,
                    semantic_reasoning TEXT,
                    verified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """
            )

    def _hash_text(self, text: str) -> str:
        return hashlib.md5(text.strip().lower().encode("utf-8")).hexdigest()

    def _fetch_cached_results(self, hashes: list) -> dict:
        if not hashes:
            return {}
        with sqlite3.connect(self.db_path) as conn:
            placeholders = ",".join(["?"] * len(hashes))
            query = f"SELECT text_hash, inferred_domain, sub_category, root_cause_hint, sentiment_urgency FROM silver_semantic_cache WHERE text_hash IN ({placeholders})"
            cursor = conn.cursor()
            cursor.execute(query, hashes)
            rows = cursor.fetchall()

        return {
            row[0]: {
                "inferred_domain": row[1],
                "sub_category": row[2],
                "root_cause_hint": row[3],
                "sentiment_urgency": row[4],
            }
            for row in rows
        }

    def _store_cache_results(self, records: list):
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO silver_semantic_cache 
                (text_hash, raw_text, inferred_domain, sub_category, root_cause_hint, sentiment_urgency)
                VALUES (:text_hash, :raw_text, :inferred_domain, :sub_category, :root_cause_hint, :sentiment_urgency);
            """,
                records,
            )

    def classify_batch(self, items: list) -> list:
        """Sends batched ticket descriptions to local LM Studio (qwen/qwen3.5-9b)."""
        system_prompt = (
            "You are an expert IT & Facility Ops Classifier. Analyze each ticket text. "
            "Output ONLY a raw JSON array of objects with these exact keys:\n"
            "- id: string (matching input id)\n"
            "- inferred_domain: string (HVAC, Electrical, Plumbing, IT, Facilities, or Fire/Safety)\n"
            "- sub_category: string (e.g. Air Conditioning, Elevator, Leaks, Network)\n"
            "- root_cause_hint: brief 3-5 word summary\n"
            "- sentiment_urgency: low, medium, high, or critical\n"
            "Do NOT include markdown backticks or commentary."
        )

        user_prompt = f"Tickets to classify:\n{json.dumps(items, indent=2)}"
        raw_response = self.llm.chat_completion(system_prompt, user_prompt)

        cleaned_text = re.sub(r"<think>.*?</think>", "", raw_response, flags=re.DOTALL).strip()
        cleaned_text = re.sub(r"^```json|^```|```$", "", cleaned_text, flags=re.MULTILINE).strip()

        try:
            parsed = json.loads(cleaned_text)
            if isinstance(parsed, list):
                return parsed
        except Exception as e:
            logger.warning(f"Classification JSON parse warning: {e}")

        return [
            {
                "id": it["id"],
                "inferred_domain": "Facilities",
                "sub_category": "General",
                "root_cause_hint": "Unspecified incident",
                "sentiment_urgency": "medium",
            }
            for it in items
        ]

    def validate_duplicate_pairs_with_llm(self) -> int:
        """Uses LM Studio to semantically verify if parent-child ticket pairs describe identical issues."""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query("SELECT ticket_id, parent_ticket_id, description, resolution_notes FROM silver_tickets WHERE duplicate_verified = 1", conn)
            all_tickets = pd.read_sql_query("SELECT ticket_id, description FROM silver_tickets", conn).set_index("ticket_id")["description"].to_dict()

        if df.empty:
            logger.info("No verified duplicate tickets found to cross-validate.")
            return 0

        logger.info(f"Submitting {len(df)} verified duplicate pairs to LLM for semantic confirmation...")

        pairs_to_check = []
        for _, row in df.iterrows():
            cid = row["ticket_id"]
            pid = row["parent_ticket_id"]
            pairs_to_check.append({
                "child_id": cid,
                "parent_id": pid,
                "child_description": row["description"],
                "parent_description": all_tickets.get(pid, "Description not found"),
                "technician_notes": row["resolution_notes"]
            })

        system_prompt = (
            "You are a Quality Assurance Auditor. Compare each child ticket with its parent ticket. "
            "Determine if they describe the exact same real-world incident or root cause. "
            "Return ONLY a JSON array with:\n"
            "- child_id: string\n"
            "- parent_id: string\n"
            "- is_true_duplicate: boolean\n"
            "- confidence: float (0.0 to 1.0)\n"
            "- reasoning: string (one concise sentence)"
        )

        user_prompt = f"Ticket pairs to evaluate:\n{json.dumps(pairs_to_check[:20], indent=2)}"
        raw_response = self.llm.chat_completion(system_prompt, user_prompt)

        cleaned_text = re.sub(r"<think>.*?</think>", "", raw_response, flags=re.DOTALL).strip()
        cleaned_text = re.sub(r"^```json|^```|```$", "", cleaned_text, flags=re.MULTILINE).strip()

        try:
            verdicts = json.loads(cleaned_text)
            with sqlite3.connect(self.db_path) as conn:
                for v in verdicts:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO silver_duplicate_llm_verification 
                        (child_ticket_id, parent_ticket_id, llm_verdict, confidence_score, semantic_reasoning)
                        VALUES (?, ?, ?, ?, ?);
                    """, (
                        v.get("child_id"),
                        v.get("parent_id"),
                        "CONFIRMED" if v.get("is_true_duplicate") else "REJECTED",
                        v.get("confidence", 1.0),
                        v.get("reasoning", "")
                    ))
                conn.commit()
            logger.info(f"Persisted {len(verdicts)} semantic duplicate audit decisions.")
            return len(verdicts)
        except Exception as e:
            logger.warning(f"Failed to parse LLM duplicate audit response: {e}")
            return 0

    def enrich_silver_records(self, max_new_inferences: int = None) -> int:
        """Enriches all silver_tickets using cache-first deduplication and LM Studio micro-batching."""
        logger.info("Starting Semantic Classification Agent enrichment...")

        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query("SELECT ticket_id, description, category FROM silver_tickets", conn)

        if df.empty:
            return 0

        df["full_text"] = (df["category"].fillna("") + ": " + df["description"].fillna("")).str.strip()
        df["text_hash"] = df["full_text"].apply(self._hash_text)

        unique_hashes = df["text_hash"].unique().tolist()
        cached_map = self._fetch_cached_results(unique_hashes)
        logger.info(f"Total rows: {len(df)} | Unique signatures: {len(unique_hashes)} | Cache hits: {len(cached_map)}")

        missing_hashes = [h for h in unique_hashes if h not in cached_map]
        if max_new_inferences is not None:
            missing_hashes = missing_hashes[:max_new_inferences]

        if missing_hashes:
            logger.info(f"Calling local LM Studio for {len(missing_hashes)} missing signatures...")
            hash_to_sample_text = (
                df[df["text_hash"].isin(missing_hashes)]
                .drop_duplicates(subset=["text_hash"])
                .set_index("text_hash")["full_text"]
                .to_dict()
            )

            batch_size = 10
            new_cache_records = []
            for i in range(0, len(missing_hashes), batch_size):
                chunk = missing_hashes[i : i + batch_size]
                items_to_send = [{"id": h, "text": hash_to_sample_text[h]} for h in chunk]
                inferences = self.classify_batch(items_to_send)

                for inf in inferences:
                    rec = {
                        "text_hash": inf.get("id"),
                        "raw_text": hash_to_sample_text.get(inf.get("id"), ""),
                        "inferred_domain": inf.get("inferred_domain", "Facilities"),
                        "sub_category": inf.get("sub_category", "General"),
                        "root_cause_hint": inf.get("root_cause_hint", "None"),
                        "sentiment_urgency": inf.get("sentiment_urgency", "medium"),
                    }
                    new_cache_records.append(rec)
                    cached_map[rec["text_hash"]] = rec

            self._store_cache_results(new_cache_records)

        # Map predictions to all records
        df["inferred_domain"] = df["text_hash"].map(lambda h: cached_map.get(h, {}).get("inferred_domain", "Miscellaneous"))
        df["sub_category"] = df["text_hash"].map(lambda h: cached_map.get(h, {}).get("sub_category", "General"))
        df["root_cause_hint"] = df["text_hash"].map(lambda h: cached_map.get(h, {}).get("root_cause_hint", ""))
        df["sentiment_urgency"] = df["text_hash"].map(lambda h: cached_map.get(h, {}).get("sentiment_urgency", "medium"))

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            for col in ["inferred_domain", "sub_category", "root_cause_hint", "sentiment_urgency"]:
                try:
                    cursor.execute(f"ALTER TABLE silver_tickets ADD COLUMN {col} VARCHAR;")
                except sqlite3.OperationalError:
                    pass

            update_data = df[["ticket_id", "inferred_domain", "sub_category", "root_cause_hint", "sentiment_urgency"]].to_dict(orient="records")
            cursor.executemany(
                """
                UPDATE silver_tickets
                SET inferred_domain = :inferred_domain,
                    sub_category = :sub_category,
                    root_cause_hint = :root_cause_hint,
                    sentiment_urgency = :sentiment_urgency
                WHERE ticket_id = :ticket_id;
            """, update_data)
            conn.commit()

        logger.info(f"Enriched all {len(df)} silver_tickets with agent classifications.")
        return len(df)


if __name__ == "__main__":
    agent = SemanticClassificationAgent()
    agent.enrich_silver_records()
    agent.validate_duplicate_pairs_with_llm()