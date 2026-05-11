"""
pipeline/parsers/sales_parser.py

Parser for Marg Silver → Stock & Sales Analysis report.
This is the movement report — shows what sold in a period.

Used for: velocity computation, dead stock detection, trend analysis.
"""

from datetime import date
from typing import Optional, List, Tuple

import pandas as pd

from config.report_schemas import get_schema
from pipeline.parsers.base_parser import BaseParser


class SalesParser(BaseParser):

    def __init__(self):
        super().__init__(schema=get_schema("stock_sales"))

    def _post_process(
        self,
        df: pd.DataFrame,
        report_date: Optional[date]
    ) -> Tuple[pd.DataFrame, List[str], List[str]]:
        errors = []
        warnings = []

        # ── Net movement ──
        if "sales_qty" in df.columns and "purchase_qty" in df.columns:
            df["net_movement"] = (
                df["purchase_qty"].fillna(0) - df["sales_qty"].fillna(0)
            )

        # ── Flag zero-movement items (no sales AND no purchases) ──
        movement_cols = ["sales_qty", "purchase_qty", "sales_return", "purchase_return"]
        present = [c for c in movement_cols if c in df.columns]
        if present:
            df["is_zero_movement"] = (
                df[present].fillna(0).abs().sum(axis=1) == 0
            )
            zero_count = df["is_zero_movement"].sum()
            if zero_count > 0:
                warnings.append(
                    f"{zero_count} items had zero movement in this period"
                )

        # ── Stock change sanity check ──
        if all(c in df.columns for c in ["opening_stock", "closing_stock", "sales_qty", "purchase_qty"]):
            expected_closing = (
                df["opening_stock"].fillna(0)
                + df["purchase_qty"].fillna(0)
                - df["sales_qty"].fillna(0)
            )
            discrepancy = (df["closing_stock"].fillna(0) - expected_closing).abs()
            bad_rows = (discrepancy > 1).sum()  # tolerance of 1 unit for rounding
            if bad_rows > 0:
                warnings.append(
                    f"{bad_rows} items have closing stock discrepancy "
                    f"(opening + purchases - sales ≠ closing). "
                    f"May indicate return/adjustment entries."
                )

        # ── Ensure code cleaned ──
        if "code" in df.columns:
            df["code"] = df["code"].astype(str).str.strip()
            header_patterns = ["code", "total", "---", "continued", "value in rs"]
            mask = df["code"].str.lower().str.contains(
                "|".join(header_patterns), na=False
            )
            dropped = mask.sum()
            if dropped:
                warnings.append(f"Dropped {dropped} non-data rows")
            df = df[~mask].reset_index(drop=True)

        return df, errors, warnings
