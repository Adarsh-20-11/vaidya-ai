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
        df = parsed_df.drop(
            columns=[c for c in parsed_df.columns if c.startswith("_")],
            errors="ignore",
        )

        # ── Categorise transaction types ──
        # Outward (reduces stock): SALE retail, ST.L wholesale, S/R return, REPL
        # Inward  (increases stock): PURC, ST.I, PRET
        # Pending  (NOT yet a stock movement): P.O. = pending purchase order
        # The agent reads `category` to distinguish retail from wholesale margins,
        # returns from actual sales, and pending orders from confirmed txns.
        OUTWARD_TYPES = {"SALE", "ST.L", "SRET", "S/R", "REPL"}
        INWARD_TYPES  = {"PURC", "ST.I", "PRET", "P/R"}
        PENDING_TYPES = {"P.O."}   # SUPERVISOR-attributed pending orders

        CATEGORY_MAP = {
            "SALE":  "retail",
            "ST.L":  "wholesale",      # stock loan to fellow medical stores
            "SRET":  "sales_return",
            "S/R":   "sales_return",   # short form of sales return (CN bills)
            "REPL":  "replacement",
            "PURC":  "purchase",
            "ST.I":  "stock_in",
            "PRET":  "purchase_return",
            "P/R":   "purchase_return",  # short form of purchase return
            "P.O.":  "pending_order",  # not a confirmed txn
        }
        df["category"] = df["transaction_type"].map(CATEGORY_MAP).fillna("unknown")

        # ── OUTWARD split (sales_entries) ──
        # Both retail SALE and wholesale ST.L go here — distinguishable by category.
        # P.O. (pending) is intentionally excluded — these aren't real txns.
        outward_df = df[df["transaction_type"].isin(OUTWARD_TYPES)].copy()
        sales_out = self._build_sales_entries(outward_df) if not outward_df.empty else pd.DataFrame()

        # ── PENDING orders dropped with a count for visibility ──
        pending_count = df["transaction_type"].isin(PENDING_TYPES).sum()
        if pending_count > 0:
            warnings.append(
                f"Skipped {pending_count} P.O. (pending purchase order) rows — "
                f"these are SUPERVISOR entries, not confirmed transactions"
            )

        # ── INWARD split (purchase_entries) ──
        inward_df = df[df["transaction_type"].isin(INWARD_TYPES)].copy()
        purchase_out = self._build_purchase_entries(inward_df) if not inward_df.empty else pd.DataFrame()

        # ── New items for master table ──
        new_items = self._build_new_items(df)

        # Detailed category counts for warning
        category_counts = df["category"].value_counts().to_dict()
        warnings.append(
            f"Split: {len(sales_out)} outward, {len(purchase_out)} inward, "
            f"{len(new_items)} unique items"
        )
        warnings.append(f"  Categories: {category_counts}")

        # ── Discount summary ──
        # Count rows with meaningful discounts (>1% to filter rounding noise)
        if not sales_out.empty and "discount_pct" in sales_out.columns:
            meaningful = sales_out["discount_pct"].fillna(0).abs() > 1
            n_discounted = int(meaningful.sum())
            if n_discounted > 0:
                avg_disc = sales_out.loc[meaningful, "discount_pct"].mean()
                max_disc = sales_out.loc[meaningful, "discount_pct"].max()
                warnings.append(
                    f"  Discounts: {n_discounted} outward rows have >1% discount "
                    f"(avg {avg_disc:.1f}%, max {max_disc:.1f}%)"
                )

        return DaybookSplit(
            sales_entries=sales_out,
            purchase_entries=purchase_out,
            new_items=new_items,
            warnings=warnings,
        )

    @staticmethod
    def _compute_discount(df: pd.DataFrame) -> pd.Series:
        """
        Compute discount % per row: how much was knocked off the qty × rate total.

        discount_pct > 0  → customer got a price break (or scheme math)
        discount_pct ≈ 0  → no discount (most rows)
        discount_pct < 0  → customer paid more (rounding, manual adjustment)

        Returns a Series aligned with df.index. NaN where computation isn't possible
        (missing qty/rate/amount, or zero expected value).
        """
        expected = df["qty"] * df["rate"]
        # Avoid divide-by-zero: replace 0 with NaN, ratio becomes NaN
        safe_expected = expected.where(expected != 0)
        discount_pct = ((expected - df["amount"]) / safe_expected * 100).round(2)
        # Cap extreme values — rate=0 or data anomalies can produce huge numbers
        discount_pct = discount_pct.clip(-9999, 9999)
        return discount_pct

    def _build_sales_entries(self, df: pd.DataFrame) -> pd.DataFrame:
        """Map outward rows → sales_entries schema. Includes category + discount + batch."""
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
            "discount_pct":   self._compute_discount(df),
            "category":       df["category"],   # retail | wholesale | sales_return | replacement
            # batch_no part of unique key — '' fallback so NULL doesn't break upserts
            "batch_no":       df["batch_no"].fillna("").astype(str),
            "expiry":         df["expiry"],
            # GST not present in daybook export — populated when GSTR-1 is added
            "gst_pct":        None,
            "gst_amount":     None,
            "total":          df["amount"],
        })

        # Drop rows that would fail uniqueness constraint (no bill_no + item_code)
        out = out.dropna(subset=["invoice_no", "item_code", "date"], how="any")

        # ── Deduplicate within batch ──
        # Marg occasionally emits two identical lines (same invoice + item + batch).
        # Aggregate them: sum qty and amount, keep first of everything else.
        # This prevents the "ON CONFLICT cannot affect row a second time" Supabase error.
        out = self._dedupe_lines(out, ["invoice_no", "item_code", "date", "batch_no"])
        return out

    def _build_purchase_entries(self, df: pd.DataFrame) -> pd.DataFrame:
        """Map inward rows → purchase_entries schema. Includes category + discount + batch."""
        out = pd.DataFrame({
            "date":         df["date"],
            "vendor_name":  df["party_name"],
            "invoice_no":   df["bill_no"],
            "item_code":    df["item_code"],
            "item_name":    df["item_name"],
            "qty":          df["qty"],
            "rate":         df["rate"],
            "amount":       df["amount"],
            "discount_pct": self._compute_discount(df),
            "category":     df["category"],   # purchase | stock_in | purchase_return
            "batch_no":     df["batch_no"].fillna("").astype(str),
            "expiry":       df["expiry"],
            "gst_pct":      None,
            "gst_amount":   None,
            "total":        df["amount"],
        })
        out = out.dropna(subset=["invoice_no", "item_code", "date"], how="any")
        out = self._dedupe_lines(out, ["invoice_no", "item_code", "date", "batch_no"])
        return out

    @staticmethod
    def _dedupe_lines(df: pd.DataFrame, key_cols: list) -> pd.DataFrame:
        """
        Collapse rows that share the same unique-key combination.
        qty and amount are summed; everything else takes the first value.

        Why: Marg sometimes outputs the same invoice+item+batch twice
        (data entry quirks). Supabase rejects batches with duplicate keys
        in a single ON CONFLICT operation.
        """
        if df.empty:
            return df

        # Check if any duplicates exist — most common path is no dupes, so short-circuit
        if not df.duplicated(subset=key_cols).any():
            return df

        # Aggregate: sum numerics that should add, keep first for everything else
        agg_map = {}
        for col in df.columns:
            if col in key_cols:
                continue
            if col in ("qty", "amount", "total"):
                agg_map[col] = "sum"
            else:
                agg_map[col] = "first"

        deduped = df.groupby(key_cols, dropna=False, as_index=False).agg(agg_map)
        return deduped

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