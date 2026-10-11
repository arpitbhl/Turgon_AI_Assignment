import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR / "Medallion Pipeline"))

from agents.data_quality_agent import DataQualityAgent
from agents.semantic_classification_agent import SemanticClassificationAgent
from bronze import run_bronze
from gold import run_gold
from silver import run_silver


def main():
    start_time = time.time()
    print("=" * 70)
    print("   MEDALLION PIPELINE WITH LOCAL LM STUDIO (qwen/qwen3.5-9b)")
    print("=" * 70)

    # 1. Bronze Ingestion
    print("\n[STEP 1] Ingesting Bronze Layer...")
    bronze_count = run_bronze()

    # 2. Agent B: Data Quality Agent
    print(
        "\n[STEP 2] Running Agent B: Data Quality Agent via LM Studio (qwen/qwen3.5-9b)..."
    )
    dq_agent = DataQualityAgent()
    dq_findings = dq_agent.run_agent(human_in_the_loop=False)
    print(
        f"  -> Agent identified {len(dq_findings.get('findings', []))} data hygiene issues."
    )
    for finding in dq_findings.get("findings", [])[:3]:
        print(
            f"     • [{finding.get('column')}]: {finding.get('issue')} (Why: {finding.get('why_it_matters')})"
        )

    # 3. Silver Cleansing
    print("\n[STEP 3] Executing Silver Cleansing & Validation Pipeline...")
    silver_count = run_silver()

    # Step 4: Agent C (Semantic Classification + Semantic Duplicate Verification)
    print("\n[STEP 4] Running Agent C: Semantic Classification & Duplicate Audit via LM Studio...")
    semantic_agent = SemanticClassificationAgent()
    
    # 4a: Enriches all records with domain and root cause
    enriched_count = semantic_agent.enrich_silver_records()
    print(f"  -> Enriched all {enriched_count} records with structured domains & root causes.")
    
    # 4b: LLM evaluates whether resolved duplicates are genuinely identical incidents
    dup_verdicts_count = semantic_agent.validate_duplicate_pairs_with_llm()
    print(f"  -> Semantically validated {dup_verdicts_count} duplicate pairs using qwen/qwen3.5-9b.")

    
    print(
        f"  -> Local Model Calls: {semantic_agent.llm.total_calls} | Tokens Processed: {semantic_agent.llm.total_tokens_consumed}"
    )

    # 5. Gold Aggregations
    print(
        "\n[STEP 5] Building Gold Analytical Marts & Aggregating Insights..."
    )
    run_gold()

    elapsed = round(time.time() - start_time, 2)
    print("\n" + "=" * 70)
    print(f" PIPELINE COMPLETED SUCCESSFULLY IN {elapsed}s")
    print(f" Bronze Raw Records    : {bronze_count}")
    print(f" Silver Valid Records  : {silver_count}")
    print(f" Dropped Junk Records  : {bronze_count - silver_count}")
    print(" Artifact Location     : data_storage/medallion.db")
    print("=" * 70)


if __name__ == "__main__":
    main()