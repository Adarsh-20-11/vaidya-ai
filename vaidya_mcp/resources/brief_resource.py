"""
resources/brief_resource.py

MCP Resources for Vaidya-AI.
Resources are read-only data the agent can access — different from tools
(which perform actions/queries). Think of resources as documents the agent
can open and read.

Resources defined here:
  vaidya://brief/today      — Pre-generated morning brief
  vaidya://pipeline/status  — Last sync time and health
  vaidya://schema/summary   — Database schema description for agent context
"""

import logging
import os
from datetime import date, datetime, timedelta

from core.database import get_supabase

logger = logging.getLogger(__name__)

BUSINESS_NAME = os.getenv("BUSINESS_NAME", "Magadh Wellness Private Limited")


def get_daily_brief_content() -> str:
    """
    Returns today's pre-generated brief, or a placeholder if not yet generated.
    The morning brief job writes to daily_briefs table at 8 AM.
    This resource lets the agent read it without regenerating it.
    """
    try:
        client = get_supabase()
        result = client.table("daily_briefs")\
            .select("content, generated_at")\
            .eq("brief_date", date.today().isoformat())\
            .execute()

        if result.data:
            brief = result.data[0]
            return (
                f"DAILY BRIEF — Generated at {brief['generated_at']}\n\n"
                f"{brief['content']}"
            )
        else:
            return (
                f"Daily brief for {date.today().isoformat()} has not been "
                f"generated yet. Run the morning brief job, or use the tools "
                f"(get_anomalies, get_stock_status) to generate a live brief."
            )

    except Exception as e:
        logger.error(f"get_daily_brief_content failed: {e}")
        return f"Could not load daily brief: {e}"


def get_pipeline_status_content() -> str:
    """
    Returns the status of the data pipeline —
    last successful sync, any failures, data freshness.
    """
    try:
        client = get_supabase()

        # Last successful run per report type
        runs = client.table("pipeline_runs")\
            .select("report_id, success, row_count, ran_at, errors")\
            .order("ran_at", desc=True)\
            .limit(20)\
            .execute()

        if not runs.data:
            return "No pipeline runs found. Has the pipeline been configured and run yet?"

        # Get latest per report_id
        seen = {}
        for run in runs.data:
            rid = run["report_id"]
            if rid not in seen:
                seen[rid] = run

        lines = [
            f"PIPELINE STATUS — as of {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC",
            ""
        ]

        for rid, run in seen.items():
            status = "✅" if run["success"] else "❌"
            ran_at = run["ran_at"]
            rows = run.get("row_count", "?")
            lines.append(f"{status} {rid}: {rows} rows | Last run: {ran_at}")
            if not run["success"] and run.get("errors"):
                lines.append(f"   Errors: {run['errors']}")

        # Check data freshness
        latest_snapshot = client.table("stock_snapshots")\
            .select("snapshot_date")\
            .order("snapshot_date", desc=True)\
            .limit(1)\
            .execute()

        if latest_snapshot.data:
            latest_date = latest_snapshot.data[0]["snapshot_date"]
            days_old = (date.today() - date.fromisoformat(latest_date)).days
            freshness = "✅ Fresh" if days_old == 0 else f"⚠️ {days_old} days old"
            lines.append(f"\nLatest stock snapshot: {latest_date} ({freshness})")

        # Snapshot count (for velocity confidence)
        count_result = client.table("stock_snapshots")\
            .select("snapshot_date", count="exact")\
            .execute()

        total_snapshots = count_result.count or 0
        unique_dates = client.table("stock_snapshots")\
            .select("snapshot_date")\
            .execute()

        dates = set(r["snapshot_date"] for r in (unique_dates.data or []))
        lines.append(f"Total snapshots: {total_snapshots} across {len(dates)} days")

        if len(dates) < 7:
            lines.append(
                f"\n⚠️ Only {len(dates)} days of data. "
                f"Velocity calculations need 7+ days. "
                f"Agent confidence will be LOW until more data accumulates."
            )
        elif len(dates) < 30:
            lines.append(
                f"\n📊 {len(dates)} days of data. "
                f"Velocity improving but 30+ days needed for full confidence."
            )
        else:
            lines.append(f"\n✅ {len(dates)} days of data. Full velocity confidence.")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"get_pipeline_status_content failed: {e}")
        return f"Could not load pipeline status: {e}"


def get_schema_summary_content() -> str:
    """
    Returns a curated description of the database schema.
    Used by the analytical agent (Phase C) for SQL generation context.
    Also useful for the operational agent to understand data boundaries.
    """
    return """
VAIDYA-AI DATABASE SCHEMA SUMMARY
Magadh Wellness Private Limited | Supabase (PostgreSQL)

CORE TABLES:
  stock_items        Master SKU list. code (PK), name, unit, category,
                     company (supplier), mrp, reorder_level
  stock_snapshots    Daily stock per item. item_code + snapshot_date (unique).
                     closing_stock, sales_qty, purchase_price, margin_pct.
                     Append-only — never updated.

TRANSACTION TABLES:
  party_ledger_entries  Financial transactions per party.
                        party_name, date, debit, credit, balance,
                        is_overdue, days_outstanding
  purchase_entries      Purchase invoices. vendor_name, item_name, qty, rate
  sales_entries         Sales invoices. customer_name, item_name, qty, rate

INTELLIGENCE TABLES (nightly computed):
  item_velocity      avg_daily_sales_7d/30d/90d, velocity_trend, confidence
  item_health        days_remaining, reorder_urgency, margin_pct, margin_status
  anomalies_today    anomaly_type, severity, detail, detected_date
  supplier_intelligence  avg_rate_30d/90d, rate_trend, items_supplied

VIEWS (use these for joins — they're pre-optimised):
  v_item_dashboard   stock_items + item_health + item_velocity joined.
                     Best for: stock status queries
  v_party_outstanding  Ledger grouped by party.
                     Best for: who owes what

SYSTEM TABLES:
  pipeline_runs      Audit log of every pipeline execution
  agent_tool_calls   Audit log of every agent query
  daily_briefs       Pre-generated morning briefs (brief_date, content)

DATA FRESHNESS:
  stock_snapshots:   Updated daily (requires manual export from Marg Silver)
  Intelligence tables: Recomputed nightly after sync
  Ledger data:       Updated when ledger export is performed (weekly recommended)
""".strip()
