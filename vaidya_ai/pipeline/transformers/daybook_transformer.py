"""
pipeline/transformers/daybook_transformer.py

Splits parsed Item Day Book records into the two destination tables:
  - sales_entries     (for transaction_type == 'SALE')
  - purchase_entries  (for transaction_type == 'PURC')

The daybook parser produces a single unified DataFrame; this transformer
produces the two-table shape that matches the Supabase schema.

It also derives a `stock_items` master update — items found in the daybook
but not yet in `stock_items` should be inserted with a placeholder.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class DaybookSplit:
    """Result of splitting daybook records into destination tables."""
    sales_entries:     pd.DataFrame
    purchase_entries:  pd.DataFrame
    new_items:         pd.DataFrame  # for stock_items upsert
    warnings:          List[str] = field(default_factory=list)


class DaybookTransformer:
    """
    Transforms parsed daybook records into the two destination tables.
    Pure DataFrame transformation — no I/O. Loader handles persistence.
    """

    def transform(self, parsed_df: pd.DataFrame) -> DaybookSplit:
        if parsed_df is None or parsed_df.empty:
            return DaybookSplit(
                sales_entries=pd.DataFrame(),
                purchase_entries=pd.DataFrame(),
                new_items=pd.DataFrame(),
                warnings=["Empty daybook input — nothing to transform"],
            )

        warnings = []

        # ── Drop pipeline metadata columns (underscore-prefixed) ──
        # These were attached by the parser but don't belong in destination tables
        df = parsed_df.drop(
            columns=[c for c in parsed_df.columns if c.startswith("_")],
            errors="ignore",
        )

        # ── SALES split ──
        sales_df = df[df["transaction_type"] == "SALE"].copy()
        sales_out = self._build_sales_entries(sales_df) if not sales_df.empty else pd.DataFrame()

        # ── PURCHASE split ──
        purc_df = df[df["transaction_type"] == "PURC"].copy()
        purchase_out = self._build_purchase_entries(purc_df) if not purc_df.empty else pd.DataFrame()

        # ── New items for master table ──
        new_items = self._build_new_items(df)

        warnings.append(
            f"Split: {len(sales_out)} sales entries, "
            f"{len(purchase_out)} purchase entries, "
            f"{len(new_items)} unique items"
        )

        return DaybookSplit(
            sales_entries=sales_out,
            purchase_entries=purchase_out,
            new_items=new_items,
            warnings=warnings,
        )

    def _build_sales_entries(self, df: pd.DataFrame) -> pd.DataFrame:
        """Map parsed SALE rows → sales_entries schema."""
        out = pd.DataFrame({
            "date":           df["date"],
            "customer_name":  df["party_name"],
            "invoice_no":     df["bill_no"],
            "item_code":      df["item_code"],
            "item_name":      df["item_name"],
            "qty":            df["qty"],
            "rate":           df["rate"],
            "mrp":            df["mrp"],
            "amount":         df["amount"],
            # GST not present in daybook export — populated when GSTR-1 is added
            "gst_pct":        None,
            "gst_amount":     None,
            "total":          df["amount"],  # gross == amount when GST absent
        })

        # Drop rows that would fail uniqueness constraint (no bill_no + item_code)
        out = out.dropna(subset=["invoice_no", "item_code", "date"], how="any")
        return out

    def _build_purchase_entries(self, df: pd.DataFrame) -> pd.DataFrame:
        """Map parsed PURC rows → purchase_entries schema."""
        out = pd.DataFrame({
            "date":         df["date"],
            "vendor_name":  df["party_name"],
            "invoice_no":   df["bill_no"],
            "item_code":    df["item_code"],
            "item_name":    df["item_name"],
            "qty":          df["qty"],
            "rate":         df["rate"],
            "amount":       df["amount"],
            "gst_pct":      None,
            "gst_amount":   None,
            "total":        df["amount"],
        })
        out = out.dropna(subset=["invoice_no", "item_code", "date"], how="any")
        return out

    def _build_new_items(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Derive unique items from the daybook for stock_items upsert.
        Uses MRP and company from the most recent transaction per item.
        """
        if df.empty:
            return pd.DataFrame()

        # Sort by date DESC so .first() picks the most recent record per item
        df_sorted = df.sort_values("date", ascending=False)

        grouped = df_sorted.groupby("item_code").first().reset_index()

        new_items = pd.DataFrame({
            "code":          grouped["item_code"],
            "name":          grouped["item_name"],
            "unit":          grouped["unit_pack"],
            "mrp":           grouped["mrp"],
            "company":       grouped["company"],
            "manufacturer":  grouped["company"],   # same value; can be split later
        })

        # Drop items with empty code (shouldn't happen but defensive)
        new_items = new_items[new_items["code"].astype(str).str.strip() != ""]
        return new_items
