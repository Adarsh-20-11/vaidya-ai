"""
pipeline/parsers/stock_parser.py

Parser for Marg Silver → Stock Report as on Date (Excel export).
This is the primary daily data source for POC.

WHAT IT DOES:
  - Resolves column aliases (Marg uses different names in different versions)
  - Flags negative stock (return adjustments, not real stockouts)
  - Flags unknown/blank suppliers
  - Computes margin_pct where possible
  - Strips '###' Excel overflow values
  - Adds derived fields used downstream by the transformer
"""

from datetime import date
from typing import Optional, List, Tuple

import pandas as pd

from config.report_schemas import get_schema
from config.settings import business_rules
from pipeline.parsers.base_parser import BaseParser


class StockParser(BaseParser):

    def __init__(self):
        super().__init__(schema=get_schema("stock_current"))

    def _post_process(
        self,
        df: pd.DataFrame,
        report_date: Optional[date]
    ) -> Tuple[pd.DataFrame, List[str], List[str]]:
        errors = []
        warnings = []

        # ── Normalise supplier name ──
        if "company" in df.columns:
            df["company"] = df["company"].apply(self._normalise_supplier)

        # ── Flag negative stock ──
        if "stock" in df.columns:
            df["is_negative_stock"] = df["stock"].fillna(0) < 0
            neg_count = df["is_negative_stock"].sum()
            if neg_count > 0:
                warnings.append(
                    f"{neg_count} items have negative stock "
                    f"(likely return adjustments — flagged, not treated as stockouts)"
                )

        # ── Flag zero stock (genuine stockouts) ──
        if "stock" in df.columns:
            df["is_zero_stock"] = df["stock"].fillna(0) == 0

        # ── Compute margin_pct ──
        if "mrp" in df.columns and "purchase_price" in df.columns:
            # Only compute where MRP > 0 (exclude zero-MRP items)
            mask = df["mrp"].fillna(0) > 0
            df["margin_pct"] = None
            df.loc[mask, "margin_pct"] = (
                (df.loc[mask, "mrp"] - df.loc[mask, "purchase_price"])
                / df.loc[mask, "mrp"]
                * 100
            ).round(2)

            zero_mrp_count = (~mask).sum()
            if zero_mrp_count > 0:
                warnings.append(
                    f"{zero_mrp_count} items have MRP=0 — excluded from margin calculation"
                )

        # ── Flag unknown supplier ──
        if "company" in df.columns:
            df["supplier_unknown"] = df["company"].isna()
            unknown_count = df["supplier_unknown"].sum()
            if unknown_count > 0:
                warnings.append(
                    f"{unknown_count} items have no supplier mapped (was -BLANK- or similar)"
                )

        # ── Flag items with stock > 0 but no value (data quality) ──
        if "stock" in df.columns and "value" in df.columns:
            suspicious = (
                (df["stock"].fillna(0) > 0) &
                (df["value"].fillna(0) == 0)
            )
            if suspicious.sum() > 0:
                warnings.append(
                    f"{suspicious.sum()} items have stock > 0 but value = 0 "
                    f"(possible costing issue)"
                )

        # ── Ensure code is string, strip whitespace ──
        if "code" in df.columns:
            df["code"] = df["code"].astype(str).str.strip()
            # Drop rows where code looks like a header or total row
            header_patterns = ["code", "total", "---", "continued"]
            mask_header = df["code"].str.lower().str.contains(
                "|".join(header_patterns), na=False
            )
            dropped = mask_header.sum()
            if dropped > 0:
                warnings.append(f"Dropped {dropped} header/footer rows detected in data")
            df = df[~mask_header].reset_index(drop=True)

        return df, errors, warnings

    def _normalise_supplier(self, value) -> Optional[str]:
        """Return None for blank/placeholder supplier values."""
        if value is None:
            return None
        s = str(value).strip()
        if s.lower() in [v.lower() for v in business_rules.unknown_supplier_values if v]:
            return None
        if not s or s == "nan":
            return None
        return s
