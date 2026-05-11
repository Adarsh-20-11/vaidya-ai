"""
run_pipeline.py

Main entry point for the Vaidya-AI data pipeline.
Called by the nightly cron job on Railway/Render.

Usage:
  python run_pipeline.py --report all
  python run_pipeline.py --report stock
  python run_pipeline.py --report ledger
  python run_pipeline.py --report daybook
  python run_pipeline.py --report all --dry-run
  python run_pipeline.py --report stock --file ./exports/stock_20260510.xlsx
  python run_pipeline.py --report daybook --file ./exports/daybook_april.csv

Pipeline order (important — don't change without understanding dependencies):
  1. stock_current  → stock_items + stock_snapshots
  2. stock_sales    → stock_snapshots (movement data)
  3. item_daybook   → sales_entries + purchase_entries + stock_items (new items)
  4. ledger         → party_ledger_entries
  5. purchase_reg   → purchase_entries  (alternate source if daybook unavailable)
  6. sales_reg      → sales_entries     (alternate source if daybook unavailable)
  7. TRANSFORM      → item_velocity, item_health, anomalies_today, supplier_intelligence
"""

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from config.settings import pipeline_config, supabase_config
from pipeline.parsers.stock_parser import StockParser
from pipeline.parsers.ledger_parser import LedgerParser
from pipeline.parsers.sales_parser import SalesParser
from pipeline.parsers.daybook_parser import DaybookParser
from pipeline.transformers.stock_transformer import StockTransformer
from pipeline.transformers.daybook_transformer import DaybookTransformer
from pipeline.loaders.supabase_loader import SupabaseLoader
from pipeline.loaders.local_loader import LocalLoader

logging.basicConfig(
    level=getattr(logging, pipeline_config.log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("run_pipeline")


# ── Table → conflict columns mapping ──
# These tell Supabase which columns define uniqueness for upsert
UPSERT_KEYS = {
    "stock_items":            ["code"],
    "stock_snapshots":        ["item_code", "snapshot_date"],
    "party_ledger_entries":   ["party_name", "date", "voucher_no"],
    "purchase_entries":       ["invoice_no", "item_code", "date"],
    "sales_entries":          ["invoice_no", "item_code", "date"],
    "item_velocity":          ["item_code"],
    "item_health":            ["item_code", "computed_date"],
    "anomalies_today":        ["item_code", "anomaly_type", "detected_date"],
    "supplier_intelligence":  ["supplier", "computed_date"],
    "pipeline_runs":          ["report_id", "ran_at"],
}


def run_stock(file_path: str, dry_run: bool, report_date: Optional[date]) -> bool:
    logger.info("── Stock Report ──")
    parser = StockParser()
    result = parser.parse(file_path, report_date)
    logger.info(result.summary())

    if not result.success:
        for e in result.errors:
            logger.error(f"  ERROR: {e}")
        return False

    for w in result.warnings:
        logger.warning(f"  WARN: {w}")

    if result.schema_mismatches:
        for m in result.schema_mismatches:
            logger.warning(f"  SCHEMA: {m}")

    if dry_run:
        LocalLoader().save("stock_current_parsed", result.data, str(report_date))
        logger.info(f"  DRY RUN: saved {result.row_count} rows locally")
        return True

    loader = SupabaseLoader()

    # stock_items master table (code is PK)
    master_cols = ["code", "name", "unit", "mrp", "company", "manufacturer", "rack_no"]
    master_df = result.data[[c for c in master_cols if c in result.data.columns]].copy()
    load_result = loader.upsert("stock_items", master_df, ["code"])
    logger.info(f"  stock_items: {load_result.rows_upserted} upserted, {load_result.errors}")

    # stock_snapshots (daily append)
    snapshot_cols = ["code", "stock", "cost", "value", "mrp", "purchase_price",
                     "sales_price", "margin_pct", "is_negative_stock", "supplier_unknown"]
    snap_df = result.data[[c for c in snapshot_cols if c in result.data.columns]].copy()
    snap_df = snap_df.rename(columns={"code": "item_code", "stock": "closing_stock"})
    snap_df["snapshot_date"] = str(report_date or date.today())
    load_result = loader.upsert("stock_snapshots", snap_df, ["item_code", "snapshot_date"])
    logger.info(f"  stock_snapshots: {load_result.rows_upserted} upserted")

    loader.log_pipeline_run(
        "stock_current", file_path, result.file_hash,
        result.row_count, result.success, result.errors, result.warnings
    )
    return True


def run_ledger(file_path: str, dry_run: bool, report_date: Optional[date]) -> bool:
    logger.info("── Ledger Report ──")
    parser = LedgerParser()
    result = parser.parse(file_path, report_date)
    logger.info(result.summary())

    if not result.success:
        for e in result.errors:
            logger.error(f"  ERROR: {e}")
        return False

    for w in result.warnings:
        logger.warning(f"  WARN: {w}")

    if dry_run:
        LocalLoader().save("ledger_parsed", result.data, str(report_date))
        return True

    loader = SupabaseLoader()
    load_result = loader.upsert(
        "party_ledger_entries", result.data,
        ["party_name", "date", "voucher_no"]
    )
    logger.info(f"  party_ledger_entries: {load_result.rows_upserted} upserted")

    loader.log_pipeline_run(
        "ledger", file_path, result.file_hash,
        result.row_count, result.success, result.errors, result.warnings
    )
    return True


def run_sales(file_path: str, dry_run: bool, report_date: Optional[date]) -> bool:
    logger.info("── Sales Analysis Report ──")
    parser = SalesParser()
    result = parser.parse(file_path, report_date)
    logger.info(result.summary())

    if not result.success:
        for e in result.errors:
            logger.error(f"  ERROR: {e}")
        return False

    for w in result.warnings:
        logger.warning(f"  WARN: {w}")

    if dry_run:
        LocalLoader().save("sales_parsed", result.data, str(report_date))
        return True

    loader = SupabaseLoader()
    snap_cols = ["code", "opening_stock", "sales_qty", "purchase_qty",
                 "closing_stock", "net_movement"]
    snap_df = result.data[[c for c in snap_cols if c in result.data.columns]].copy()
    snap_df = snap_df.rename(columns={"code": "item_code"})
    snap_df["snapshot_date"] = str(report_date or date.today())

    load_result = loader.upsert("stock_snapshots", snap_df, ["item_code", "snapshot_date"])
    logger.info(f"  stock_snapshots (sales): {load_result.rows_upserted} upserted")

    loader.log_pipeline_run(
        "stock_sales", file_path, result.file_hash,
        result.row_count, result.success, result.errors, result.warnings
    )
    return True


def run_daybook(file_path: str, dry_run: bool, report_date: Optional[date]) -> bool:
    """
    Item Day Book is the richest single export — it contains every SALE
    and PURC line across all bills in a date range. Splits into both
    sales_entries and purchase_entries.
    """
    logger.info("── Item Day Book ──")
    parser = DaybookParser()
    result = parser.parse(file_path, report_date)
    logger.info(result.summary())

    if not result.success:
        for e in result.errors:
            logger.error(f"  ERROR: {e}")
        return False

    for w in result.warnings:
        logger.warning(f"  WARN: {w}")

    # Split parsed records into destination tables
    transformer = DaybookTransformer()
    split = transformer.transform(result.data)

    for w in split.warnings:
        logger.info(f"  {w}")

    if dry_run:
        local = LocalLoader()
        local.save("daybook_parsed", result.data, str(report_date))
        local.save("daybook_sales", split.sales_entries, str(report_date))
        local.save("daybook_purchases", split.purchase_entries, str(report_date))
        local.save("daybook_new_items", split.new_items, str(report_date))
        logger.info(f"  DRY RUN: saved 4 files locally")
        return True

    loader = SupabaseLoader()

    # 1. Upsert new items into stock_items master
    if not split.new_items.empty:
        r = loader.upsert("stock_items", split.new_items, ["code"])
        logger.info(f"  stock_items: {r.rows_upserted} upserted")

    # 2. Upsert sales_entries
    if not split.sales_entries.empty:
        r = loader.upsert(
            "sales_entries", split.sales_entries,
            ["invoice_no", "item_code", "date"]
        )
        logger.info(f"  sales_entries: {r.rows_upserted} upserted")

    # 3. Upsert purchase_entries
    if not split.purchase_entries.empty:
        r = loader.upsert(
            "purchase_entries", split.purchase_entries,
            ["invoice_no", "item_code", "date"]
        )
        logger.info(f"  purchase_entries: {r.rows_upserted} upserted")

    loader.log_pipeline_run(
        "item_daybook", file_path, result.file_hash,
        result.row_count, result.success, result.errors, result.warnings
    )
    return True


def run_transform(dry_run: bool) -> bool:
    """
    Pull latest snapshots from Supabase and compute intelligence tables.
    Skipped on dry_run (no Supabase data to read).
    """
    logger.info("── Intelligence Transform ──")

    if dry_run:
        logger.info("  DRY RUN: skipping transform (no Supabase data in dry-run mode)")
        return True

    try:
        from supabase import create_client
        client = create_client(supabase_config.url, supabase_config.key)
        response = client.table("stock_snapshots").select("*").execute()
        snapshots = pd.DataFrame(response.data)
    except Exception as e:
        logger.error(f"  Failed to fetch snapshots for transform: {e}")
        return False

    if snapshots.empty:
        logger.warning("  No snapshot data found — skipping transform")
        return True

    transformer = StockTransformer()
    result = transformer.transform(snapshots)

    if not result.success:
        for e in result.errors:
            logger.error(f"  ERROR: {e}")
        return False

    loader = SupabaseLoader()
    for table_name, df in result.tables.items():
        if df.empty:
            continue
        conflict_cols = UPSERT_KEYS.get(table_name, ["item_code"])
        load_result = loader.upsert(table_name, df, conflict_cols)
        logger.info(f"  {table_name}: {load_result.rows_upserted} upserted")

    return True


def main():
    parser = argparse.ArgumentParser(description="Vaidya-AI Data Pipeline")
    parser.add_argument(
        "--report",
        choices=["all", "stock", "ledger", "sales", "daybook"],
        default="all"
    )
    parser.add_argument("--file", type=str, help="Specific file to process (optional)")
    parser.add_argument("--date", type=str, help="Report date YYYY-MM-DD (default: today)")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no Supabase writes")
    args = parser.parse_args()

    dry_run = args.dry_run or pipeline_config.dry_run
    report_date = (
        date.fromisoformat(args.date) if args.date else date.today()
    )

    logger.info(f"Vaidya-AI Pipeline | report={args.report} | date={report_date} | dry_run={dry_run}")

    export_dir = Path(pipeline_config.export_dir)
    success = True

    def find_file(prefix: str, extensions: tuple = (".xlsx",)) -> Optional[str]:
        """Find a matching file in export_dir. Defaults to .xlsx; daybook uses .csv."""
        if args.file:
            return args.file
        date_str = report_date.strftime("%Y%m%d")
        for ext in extensions:
            candidates = list(export_dir.glob(f"*{prefix}*{date_str}*{ext}"))
            if not candidates:
                candidates = list(export_dir.glob(f"*{prefix}*{ext}"))
            if candidates:
                return str(candidates[0])
        return None

    if args.report in ("all", "stock"):
        f = find_file("stock")
        if f:
            success &= run_stock(f, dry_run, report_date)
        else:
            logger.warning("No stock file found in export directory")

    if args.report in ("all", "ledger"):
        f = find_file("ledger")
        if f:
            success &= run_ledger(f, dry_run, report_date)
        else:
            logger.info("No ledger file found (optional for today)")

    if args.report in ("all", "sales"):
        f = find_file("sales")
        if f:
            success &= run_sales(f, dry_run, report_date)
        else:
            logger.info("No sales analysis file found (optional for today)")

    if args.report in ("all", "daybook"):
        # Daybook is CSV, not xlsx
        f = find_file("daybook", extensions=(".csv",))
        if f:
            success &= run_daybook(f, dry_run, report_date)
        else:
            logger.info("No daybook file found (optional for today)")

    if args.report == "all":
        success &= run_transform(dry_run)

    logger.info(f"Pipeline complete | success={success}")
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
