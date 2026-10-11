# AI-Assisted Medallion Pipeline

An end-to-end data pipeline implementing a Medallion Architecture (Bronze $\rightarrow$ Silver $\rightarrow$ Gold) over operational facility and IT support tickets (`Data/raw_tickets.csv` — ~10k rows). The system is accelerated by local AI agents interfacing with **LM Studio** (`qwen/qwen3.5-9b`) to automate data quality diagnostics, repair code synthesis, cross-reference deduplication, and semantic entity classification.

---

## 1. Architecture Diagram


```
                      Data/raw_tickets.csv (~10k rows)
                                      │
                                      ▼
                  ┌───────────────────────────────────────┐
                  │             BRONZE LAYER              │
                  │  Schema-on-read, raw data retention   │
                  │  Lineage: _source_file, _ingested_at, │
                  │           _row_hash (SHA-256)         │
                  │  Table: bronze_tickets                │
                  └───────────────────┬───────────────────┘
                                      │
                                      ├────────────────────────────────────────┐
                                      ▼                                        ▼
                  ┌───────────────────────────────────────┐        ┌───────────────────────┐
                  │       AGENT B: DATA QUALITY AGENT     │        │  Human Approval Gate  │
                  │  Profiles nulls, cardinality, pings.  │        │  (Interactive Review) │
                  │  Generates patches & explanations.    │        └───────────────────────┘
                  │  Tables: bronze_quality_audit,        │
                  │          bronze_quality_patches       │
                  └───────────────────┬───────────────────┘
                                      │
                                      ▼
                  ┌───────────────────────────────────────┐
                  │             SILVER LAYER              │
                  │  • Deduplication (_row_hash, tkt_id)  │
                  │  • Filter monitor pings & test users  │
                  │  • Canonical entity mapping (names)   │
                  │  • Standardize priorities & taxonomy  │
                  │  • Multi-format datetime parsing      │
                  │  • Numeric scrubbing (cost, sla_hours)│
                  │  • Bidirectional ticket reconciliation│
                  │    via notes regex (#10623 / TKT-*)   │
                  │  Table: silver_tickets                │
                  └───────────────────┬───────────────────┘
                                      │
                                      ▼
                  ┌───────────────────────────────────────┐
                  │    AGENT C: SEMANTIC CLASSIFICATION   │
                  │  • Hash-deduplicated text signatures  │
                  │  • Persistent SQLite prompt cache     │
                  │  • Micro-batched inference (LM Studio)│
                  │  • Semantic duplicate pair auditing   │
                  │  Tables: silver_semantic_cache,       │
                  │          silver_duplicate_llm_verif   │
                  └───────────────────┬───────────────────┘
                                      │
                                      ▼
                  ┌───────────────────────────────────────┐
                  │              GOLD LAYER               │
                  │  Business Marts (duplicate_verified=0)│
                  │  • gold_category_sla_metrics          │
                  │  • gold_agent_performance             │
                  │  • gold_building_facility_metrics     │
                  └───────────────────────────────────────┘

```

---

## 2. Directory Structure

```text
.
├── Data/
│   └── raw_tickets.csv                     # Raw operational ticket records (~10k rows)
├── Medallion Pipeline/
│   ├── bronze.py                           # Bronze layer: raw ingestion & audit lineage
│   ├── silver.py                           # Silver layer: cleansing, typing, bidirectional dedup
│   ├── gold.py                             # Gold layer: analytical business marts
│   └── run_pipeline.py                     # Deterministic execution runner
├── agents/
│   ├── __init__.py
│   ├── llm_client.py                       # LM Studio OpenAI-compatible client wrapper
│   ├── data_quality_agent.py               # Agent B: Profiler, diagnostics, patch generator
│   └── semantic_classification_agent.py    # Agent C: Cached semantic classifier & duplicate auditor
├── data_storage/
│   └── medallion.db                        # SQLite database artifact
├── run_agentic_pipeline.py                 # Master orchestrator combining layers and AI agents
├── requirements.txt
└── README.md

```

---

## 3. How to Run

### Step 1: Install Dependencies

Requires Python 3.9+:

```bash
pip install -r requirements.txt

```

### Step 2: Start Local LM Studio Server

1. Launch **LM Studio**.
2. Download and load `qwen/qwen3.5-9b` (or a compatible Qwen 2.5/3.5 variant).
3. Open the **Developer / Local Server** tab (`<->` icon).
4. Select `qwen/qwen3.5-9b` from the model selector.
5. Click **Start Server** (default: `http://localhost:1234/v1`).

### Step 3: Run the Pipeline

To run the complete AI-augmented pipeline end-to-end:

```bash
python run_agentic_pipeline.py

```

To run only the deterministic data processing stages (without triggering agent LLM calls):

```bash
python "Medallion Pipeline/run_pipeline.py"

```

The pipeline executes **idempotently** — tables are reconstructed and refreshed deterministically without duplicate key collisions or schema corruption.

---

## 4. Part 1: Medallion Architecture Implementation

### Bronze Layer (`bronze.py` $\rightarrow$ `bronze_tickets`)

* **Objective:** Raw ingestion with zero data loss.
* **Schema-on-Read:** Reads all incoming columns as strings to prevent premature conversion errors or truncation during ingestion.
* **Lineage & Audit Metadata:**
* `_source_file`: Identifies the origin file (`raw_tickets.csv`).
* `_ingested_at`: UTC ISO timestamp marking the exact ingestion moment.
* `_row_hash`: SHA-256 hash computed across all column values for deterministic row tracking.



---

### Silver Layer (`silver.py` $\rightarrow$ `silver_tickets`)

* **Objective:** Cleansed, typed, normalized, and validated analytical records.
* **Cleansing Rules & Justifications:**
1. **Deduplication:** Removes byte-for-byte row duplicates via `_row_hash` and duplicate logical entities via `ticket_id` (retaining the latest).
2. **Automated Submission Filtering:** Excludes monitor pings, system errors, and synthetic load tests where `submitted_by` is blank or matches `test`, `system`, or `admin`.
3. **Index Re-alignment:** Applies `df.reset_index(drop=True)` immediately after row filtering to guarantee contiguous index labels for reliable downstream operations.
4. **Canonical Entity Resolution:** Normalizes shorthand technician names across `assigned_to` and `submitted_by` (e.g., `"a. garcia"` $\rightarrow$ `"alex garcia"`, `"bob martinez"` / `"r. martinez"` $\rightarrow$ `"robert martinez"`, `"t. wilson"` $\rightarrow$ `"tom wilson"`).
5. **Priority Normalization:** Maps abbreviations (`crit`, `hi`, `med`, `lo`) to standard categorical values (`critical`, `high`, `medium`, `low`).
6. **Hierarchical Categorization:** Maps free-text inputs into standardized operational taxonomy:
* *HVAC:* `HVAC - Air Conditioning`, `HVAC - Heating`, `HVAC - Ventilation`, `HVAC - Others`
* *Electrical:* `Electrical System - Elevator`, `Electrical Systems - Escalator`, `Electrical System - Others`
* *Facilities & Safety:* `Plumbing`, `Fire/Safety`, `Pest Control`, `HouseKeeping`, `IT Support`, `Power Issue`, `Maintenance`
* *Fallback:* Any unmatched value is classified as `Miscellaneous`, and the original category label is appended to `description` (`<description> | Category_description: <original_value>`).


7. **Multi-Format Datetime Normalization:** Standardizes disparate string formats (`2024-10-24 14:32:10`, `12-Feb-2025 3:21`, `02/14/2025 8:16 AM`, `2025-07-01`) into a single ISO standard: `YYYY-MM-DD HH:MM:SS`.
8. **Numeric Cleaning (`cost` & `sla_hours`):**
* Strips currency characters (`$`), text units (`hours`, `hrs`), and whitespace.
* Maps `TBD`, `N/A`, and empty entries to `-1.0` as an explicit sentinel value indicating missing numeric records (avoiding misleading `0.0` values).


9. **Bidirectional Parent $\leftrightarrow$ Child Ticket Reconciliation:**
* Extracts parent references from resolution notes matching patterns like `"Duplicate of ticket #10623. Closing."` or `"Dup of TKT-10623"`.
* Validates that the referenced parent exists in the dataset.
* **Parent $\rightarrow$ Child backfill:** Child tickets inherit missing `category`, `assigned_to`, `cost`, and `sla_hours` from the parent.
* **Child $\rightarrow$ Parent backfill:** If the parent ticket contains missing/unassigned fields (`cost <= 0`, `sla_hours <= 0`, `assigned_to == 'unknown'`), valid details from the child ticket are backfilled to the parent.
* Tags records with `is_duplicate = 1`, `parent_ticket_id = 'TKT-10623'`, and `duplicate_verified = 1`.





---

### Gold Layer (`gold.py` $\rightarrow$ Business Aggregation Marts)

All Gold marts enforce `WHERE duplicate_verified = 0` to filter out verified duplicate child tickets, preventing redundant entries from inflating operational volume or maintenance expenditure:

| Table Name | Business Justification | Output Columns |
| --- | --- | --- |
| `gold_category_sla_metrics` | Measures failure frequency, SLA turnaround, and verified repair expenditures per operational domain. | `category`, `priority`, `total_tickets`, `resolved_tickets`, `resolution_rate_pct`, `total_valid_cost`, `avg_sla_hours` |
| `gold_agent_performance` | Assesses workload distribution, resolved volume, active queue backlogs, and completion rates per technician. | `assigned_to`, `total_assigned`, `tickets_completed`, `open_backlog`, `completion_rate_pct` |
| `gold_building_facility_metrics` | Pinpoints facility infrastructure wear by aggregating incident frequencies and valid repair costs per building. | `building`, `incident_count`, `total_repair_cost`, `avg_cost_per_incident` |

---

## 5. Part 2: Agent Assessment

### Agent B: Data Quality & Profiling Agent (`agents/data_quality_agent.py`)

* **What it does:** Profiles the Bronze layer (null distributions, cardinality, and value samples), submits the profile to LM Studio (`qwen/qwen3.5-9b`), documents *why* anomalies impact business metrics, and generates executable Python/pandas cleaning code.
* **Storage Location:** Findings and generated patch code are stored in SQLite tables `bronze_quality_audit` and `bronze_quality_patches`.
* **Sample Input:**
```json
{
  "column": "submitted_by",
  "null_rate_pct": 5.4,
  "cardinality": 42,
  "sample_distinct_values": ["test", "admin", "system", "d. kim", ""]
}

```


* **Sample Output:**
```json
{
  "findings": [
    {
      "column": "submitted_by",
      "issue": "Found automated/test harnesses ('test', 'system', 'admin') and empty records.",
      "why_it_matters": "Distorts technician performance and inflates ticket volume SLAs with synthetic telemetry.",
      "remedy": "Filter rows matching test/system/admin/empty strings."
    }
  ],
  "executable_python_patch": "df = df[~df['submitted_by'].str.lower().isin(['test', 'system', 'admin'])]"
}

```


* **Honest Assessment:**
> **Verdict: High Value.** Profiling 10,000 messy rows manually often takes 45–60 minutes of writing exploratory pandas one-liners. Having an agent extract distinct value patterns, explain the downstream business impact, and scaffold the cleaning logic cut exploratory time by ~75%. An interactive human approval gate ensures the proposed code is reviewed before execution.



---

### Agent C: Semantic Classification & Normalization Agent (`agents/semantic_classification_agent.py`)

* **What it does:**
1. Enriches unstructured `description` and ambiguous `category` fields into queryable structured attributes: `inferred_domain`, `sub_category`, `root_cause_hint`, and `sentiment_urgency`.
2. Semantically verifies whether ticket pairs marked as duplicates in `resolution_notes` actually describe the identical physical issue.


* **Scale-Aware Architecture:**
* **Hash-based Semantic Deduplication:** Groups identical/boilerplate descriptions into a single MD5 signature.
* **Persistent SQLite Cache (`silver_semantic_cache`):** Ensures re-running the pipeline incurs zero redundant model calls.
* **Micro-Batching:** Groups up to 10 unique signatures per request to maximize local GPU/CPU throughput on `qwen/qwen3.5-9b`.


* **Storage Location:** Classified attributes are written to `silver_tickets`, and pair verification reasoning is saved in `silver_duplicate_llm_verification`.
* **Sample Input:**
```text
"Category: elevator | Description: The main passenger lift in North Tower is stuck between floors 3 and 4 with passengers inside"

```


* **Sample Output:**
```json
{
  "inferred_domain": "Electrical",
  "sub_category": "Elevator",
  "root_cause_hint": "Passenger lift entrapment",
  "sentiment_urgency": "critical"
}

```


* **Honest Assessment:**
> **Verdict: Nuanced Value (Valuable for Entity Extraction, Impractical if Naively Applied).**
> Calling an LLM row-by-row on 10,000+ tickets is slow and expensive. However, combining **hash-based deduplication with persistent SQLite caching** dropped unique text signatures by ~65%, making it fast and practical on a local 9B model. It reliably extracts subtle root causes that simple regular expressions miss.



---

## 6. Analytical Limitations & Caveats in Gold Metrics

While the Gold layer supplies key directional metrics, the following data constraints must be considered:

### 1. Incomplete Cost & SLA Metrics (`TBD` / `N/A` / `NULL`)

* In the Silver layer, unbilled or unknown values were marked with the sentinel value `-1.0`. The Gold marts filter these out (`cost > 0`, `sla_hours > 0`) when computing averages and sums.
* **Why values cannot be imputed from existing rows:** Maintenance, electrical, and HVAC incidents do not bill against a flat hourly rate. Charges depend on equipment parts replacement, third-party vendor dispatch fees, after-hours emergency surcharges, and specialized labor. Imputing `cost` or `sla_hours` via mean, median, or regression across different problem types would skew financial reporting.

### 2. Unresolved Semantic Duplicates in Free Text

* While regex extracts references like `Duplicate of ticket #10623`, tickets logged independently by different users reporting the exact same real-world failure without cross-referencing each other remain separate rows unless caught by semantic clustering.

---

## 7. What Changes at 100x Scale (1M+ Rows & Daily Incremental Loads)?

1. **Storage & Compute Engine:**
* Replace SQLite with **DuckDB** (for single-node analytical workloads) or **Snowflake / Databricks / PostgreSQL** for multi-tenant pipelines.
* Switch pandas transformations to **Polars** or **Apache Spark** to run distributed, out-of-core transforms.


2. **Agent Decoupling & Asynchronous Inference:**
* Move the LLM Semantic Agent out of the synchronous data path into an asynchronous worker pool (e.g., Celery, AWS SQS + Lambda) with Redis caching.
* The core pipeline processes standard structured rows immediately, while unclassified text signatures are processed asynchronously and merged back during micro-batches.


3. **Incremental Ingestion (Watermarking & Change Data Capture):**
* Rather than rebuilding tables with `CREATE OR REPLACE`, implement append-only landing partitions in Bronze using ingestion date partitions (`/year=2026/month=10/`).
* Use an `updated_at` watermark to run incremental Silver upserts (`MERGE INTO silver_tickets USING bronze_tickets_incremental ON ticket_id`).


4. **Data Quality Gate Automation:**
* Move the Data Quality Agent from an interactive Python script to a scheduled **Great Expectations** or **Soda Core** test suite, running automated circuit-breaker checks before promoting data to Gold.
