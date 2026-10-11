import logging
from pathlib import Path
import sqlite3

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("GoldLayer")

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data_storage" / "medallion.db"


def run_gold():
    logger.info("Starting Gold layer aggregation & business marts (filtering out duplicates)...")

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        # Mart 1: Category & Priority Resolution Performance
        # Excludes duplicate tickets (duplicate_verified = 0) to prevent double-counting volume & costs
        cursor.execute("DROP TABLE IF EXISTS gold_category_sla_metrics;")
        cursor.execute(
            """
            CREATE TABLE gold_category_sla_metrics AS
            SELECT
                category,
                priority,
                COUNT(*) AS total_tickets,
                SUM(CASE WHEN status IN ('closed', 'resolved') THEN 1 ELSE 0 END) AS resolved_tickets,
                ROUND(
                    SUM(CASE WHEN status IN ('closed', 'resolved') THEN 1.0 ELSE 0.0 END) * 100.0 / COUNT(*),
                    2
                ) AS resolution_rate_pct,
                ROUND(SUM(CASE WHEN cost > 0 THEN cost ELSE 0 END), 2) AS total_valid_cost,
                ROUND(AVG(CASE WHEN sla_hours > 0 THEN sla_hours ELSE NULL END), 2) AS avg_sla_hours
            FROM silver_tickets
            WHERE duplicate_verified = 0
            GROUP BY category, priority
            ORDER BY total_tickets DESC;
        """
        )

        # Mart 2: Support Staff / Assignee Performance
        # Only tracks authentic incidents, ignoring redundant duplicate tickets
        cursor.execute("DROP TABLE IF EXISTS gold_agent_performance;")
        cursor.execute(
            """
            CREATE TABLE gold_agent_performance AS
            SELECT
                assigned_to,
                COUNT(*) AS total_assigned,
                SUM(CASE WHEN status IN ('closed', 'resolved') THEN 1 ELSE 0 END) AS tickets_completed,
                SUM(CASE WHEN status NOT IN ('closed', 'resolved') THEN 1 ELSE 0 END) AS open_backlog,
                ROUND(
                    SUM(CASE WHEN status IN ('closed', 'resolved') THEN 1.0 ELSE 0.0 END) * 100.0 / COUNT(*),
                    2
                ) AS completion_rate_pct
            FROM silver_tickets
            WHERE duplicate_verified = 0
            GROUP BY assigned_to
            ORDER BY open_backlog DESC;
        """
        )

        # Mart 3: Facility / Building Maintenance & Expenditure Mart
        # Prevents duplicate tickets from inflating incident frequencies and repair spend
        cursor.execute("DROP TABLE IF EXISTS gold_building_facility_metrics;")
        cursor.execute(
            """
            CREATE TABLE gold_building_facility_metrics AS
            SELECT
                building,
                COUNT(*) AS incident_count,
                ROUND(SUM(CASE WHEN cost > 0 THEN cost ELSE 0 END), 2) AS total_repair_cost,
                ROUND(AVG(CASE WHEN cost > 0 THEN cost ELSE NULL END), 2) AS avg_cost_per_incident
            FROM silver_tickets
            WHERE duplicate_verified = 0
            GROUP BY building
            ORDER BY incident_count DESC;
        """
        )

        conn.commit()

        m1 = cursor.execute("SELECT COUNT(*) FROM gold_category_sla_metrics").fetchone()[0]
        m2 = cursor.execute("SELECT COUNT(*) FROM gold_agent_performance").fetchone()[0]
        m3 = cursor.execute("SELECT COUNT(*) FROM gold_building_facility_metrics").fetchone()[0]

    logger.info(
        f"Gold layer complete (duplicates excluded). Mart counts -> Category: {m1}, Agent: {m2}, Building: {m3}"
    )


if __name__ == "__main__":
    run_gold()