import json
import logging
from pathlib import Path
import re
import sqlite3
import pandas as pd
from .llm_client import LLMClient

logger = logging.getLogger("DataQualityAgent")
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data_storage" / "medallion.db"


class DataQualityAgent:

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.llm = LLMClient()
        self._init_audit_table()

    def _init_audit_table(self):
        """Initializes tables to store LLM diagnostic findings and generated patch code."""
        with sqlite3.connect(self.db_path) as conn:
            # Table 1: Itemized column issues and justifications
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bronze_quality_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    column_name TEXT,
                    issue TEXT,
                    why_it_matters TEXT,
                    remedy TEXT,
                    audited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """
            )
            # Table 2: Generated executable patch scripts per audit run
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bronze_quality_patches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_name TEXT,
                    executable_python_patch TEXT,
                    total_findings_count INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """
            )

    def save_findings_to_db(self, findings: list, patch_code: str):
        """Persists agent diagnostic findings and executable patch script into SQLite."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # 1. Save itemized diagnostic findings
            if findings:
                for f in findings:
                    cursor.execute(
                        """
                        INSERT INTO bronze_quality_audit (column_name, issue, why_it_matters, remedy)
                        VALUES (?, ?, ?, ?);
                    """,
                        (
                            f.get("column", "unknown"),
                            f.get("issue", ""),
                            f.get("why_it_matters", ""),
                            f.get("remedy", ""),
                        ),
                    )

            # 2. Save the executable python patch script
            if patch_code:
                cursor.execute(
                    """
                    INSERT INTO bronze_quality_patches (model_name, executable_python_patch, total_findings_count)
                    VALUES (?, ?, ?);
                """,
                    (self.llm.model_name, patch_code, len(findings or [])),
                )

            conn.commit()

        logger.info(
            f"Saved {len(findings or [])} findings and executable patch into SQLite."
        )

    def profile_bronze_layer(self, sample_size: int = 5000) -> dict:
        """Calculates statistical metrics from the raw bronze table."""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(
                f"SELECT * FROM bronze_tickets LIMIT {sample_size}", conn
            )

        profile = {
            "total_sampled_rows": len(df),
            "columns": {},
        }

        for col in df.columns:
            if col.startswith("_"):
                continue
            series = df[col].astype(str).str.strip()
            null_count = int(
                series.isin(["", "none", "nan", "null"]).sum()
                + df[col].isna().sum()
            )
            unique_vals = series.unique()

            profile["columns"][col] = {
                "null_rate_pct": round((null_count / len(df)) * 100, 2),
                "cardinality": len(unique_vals),
                "sample_distinct_values": unique_vals[:10].tolist(),
            }

        return profile

    def run_agent(self, human_in_the_loop: bool = False) -> dict:
        """Profiles bronze data, submits summary to LM Studio, saves findings & patch to DB."""
        logger.info("Profiling Bronze layer statistics...")
        profile_stats = self.profile_bronze_layer()

        system_prompt = (
            "You are an expert Data Reliability Engineer. Analyze the provided dataset profile statistics. "
            "Identify anomalies (casing splits, abbreviations, sentinel nulls like 'TBD', automated test pings). "
            "Respond ONLY with a valid JSON object without surrounding markdown commentary. "
            "The JSON must have the following keys:\n"
            "{\n"
            '  "findings": [\n'
            '     {"column": "...", "issue": "...", "why_it_matters": "...", "remedy": "..."}\n'
            "  ],\n"
            '  "executable_python_patch": "..."\n'
            "}"
        )

        user_prompt = (
            f"PROFILER_OUTPUT:\n{json.dumps(profile_stats, indent=2)}\n\n"
            "Diagnose the data cleanliness issues and generate validation/cleaning rules."
        )

        logger.info(
            f"Submitting profile summary to LM Studio ({self.llm.model_name})..."
        )
        response_text = self.llm.chat_completion(system_prompt, user_prompt)

        # Remove thinking/reasoning tags if generated by Qwen models
        cleaned_text = re.sub(
            r"<think>.*?</think>", "", response_text, flags=re.DOTALL
        ).strip()
        cleaned_text = re.sub(
            r"^```json|^```|```$", "", cleaned_text, flags=re.MULTILINE
        ).strip()

        try:
            result = json.loads(cleaned_text)
        except Exception as e:
            logger.warning(
                f"JSON parse error on LLM output ({e}). Preserving raw output."
            )
            result = {
                "findings": [],
                "raw_response": response_text,
                "executable_python_patch": "",
            }

        findings = result.get("findings", [])
        patch_code = result.get("executable_python_patch", "")

        # Persist both findings and patch script to the database
        if findings or patch_code:
            self.save_findings_to_db(findings, patch_code)

        if human_in_the_loop:
            print("\n" + "=" * 60)
            print("HUMAN APPROVAL GATE - DATA QUALITY AGENT FINDINGS")
            print("=" * 60)
            for f in findings:
                print(f"• Column [{f.get('column')}]: {f.get('issue')}")
                print(f"  WHY IT MATTERS: {f.get('why_it_matters')}")
                print(f"  RECOMMENDED ACTION: {f.get('remedy')}\n")
            if patch_code:
                print("GENERATED PYTHON PATCH:\n" + "-" * 30)
                print(patch_code)
                print("-" * 30)
            input("Press Enter to approve agent findings and proceed...")

        return result


if __name__ == "__main__":
    agent = DataQualityAgent()
    analysis = agent.run_agent(human_in_the_loop=False)
    print(json.dumps(analysis, indent=2))