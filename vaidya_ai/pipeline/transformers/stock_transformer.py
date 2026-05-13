"""
pipeline/transformers/stock_transformer.py

Computes intelligence tables from raw stock snapshots.
Runs nightly AFTER the parser has loaded data into Supabase.

Produces:
  - item_health     (days remaining, urgency, margin status)
  - item_velocity   (avg daily sales across windows)
  - anomalies_today (flagged items needing attention)
  - supplier_intelligence (rate trends per supplier)

These are what the AI agent queries — never the raw snapshots.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional, Dict, Any

import pandas as pd

from config.settings import business_rules

logger = logging.getLogger(__name__)


@dataclass
class TransformResult:
    success: bool
    tables: Dict[str, pd.DataFrame] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    computed_at: datetime = field(default_factory=datetime.utcnow)


class StockTransformer:
    """
    Transforms raw stock_snapshots into intelligence tables.
    Input:  DataFrame of stock_snapshots (multiple dates, multiple items)
    Output: TransformResult with item_health, item_velocity, anomalies_today,
            supplier_intelligence DataFrames
    """

    def transform(
        self,
        snapshots: pd.DataFrame,
        as_of_date: Optional[date] = None,
        suppliers: Optional[pd.DataFrame] = None,
        stock_items: Optional[pd.DataFrame] = None,
    ) -> TransformResult:
        """
        Main entry point.
        snapshots:   item_code, snapshot_date, closing_stock, sales_qty,
                     purchase_price, sales_price, cost, value, company
        suppliers:   optional [supplier, lead_time_days] for reorder_point
        stock_items: optional [code, name, company, category] dimension table
                     Used for name-based filtering (dead stock keywords).
                     Names are NOT stored in output tables — views handle that.
        """
        as_of = as_of_date or date.today()
        errors = []
        warnings = []
        tables = {}

        if snapshots.empty:
            return TransformResult(
                success=False,
                errors=["No snapshot data to transform"]
            )

        # Build item_name and category lookup from stock_items dimension
        # Used internally for filtering only — never written to output tables
        if stock_items is not None and not stock_items.empty:
            item_dim = stock_items.set_index("code").to_dict("index")
        else:
            item_dim = {}

        # Ensure date column is datetime
        snapshots["snapshot_date"] = pd.to_datetime(snapshots["snapshot_date"])

        # Latest snapshot per item (for current state)
        latest = (
            snapshots.sort_values("snapshot_date")
            .groupby("item_code")
            .last()
            .reset_index()
        )

        # ── Velocity ──
        velocity_df, vel_warnings = self._compute_velocity(snapshots, as_of)
        warnings.extend(vel_warnings)
        tables["item_velocity"] = velocity_df

        # ── Health ──
        health_df, health_warnings = self._compute_health(
            latest, velocity_df, as_of, suppliers=suppliers
        )
        warnings.extend(health_warnings)
        tables["item_health"] = health_df

        # ── Anomalies ──
        anomalies_df = self._compute_anomalies(
            latest, health_df, snapshots, as_of, item_dim=item_dim
        )
        tables["anomalies_today"] = anomalies_df

        # ── Supplier intelligence ──
        supplier_df, sup_warnings = self._compute_supplier_intelligence(snapshots)
        warnings.extend(sup_warnings)
        tables["supplier_intelligence"] = supplier_df

        for name, df in tables.items():
            logger.info(f"  [transformer] {name}: {len(df)} rows computed")

        return TransformResult(
            success=True,
            tables=tables,
            errors=errors,
            warnings=warnings
        )

    def _log_table_sizes(self, tables):
        for name, df in tables.items():
            logger.debug(f"  transformer output {name}: {len(df)} rows")

    def _compute_velocity(
        self, snapshots: pd.DataFrame, as_of: date
    ):
        """
        Compute average daily sales over 7, 30, 90 day windows.
        Only computes if enough data exists for the window.
        """
        warnings = []
        results = []

        for item_code, group in snapshots.groupby("item_code"):
            group = group.sort_values("snapshot_date")
            row = {"item_code": item_code}

            for window in [
                business_rules.velocity_short_window,
                business_rules.velocity_medium_window,
                business_rules.velocity_long_window
            ]:
                cutoff = pd.Timestamp(as_of) - pd.Timedelta(days=window)
                window_data = group[group["snapshot_date"] >= cutoff]

                col = f"avg_daily_sales_{window}d"
                conf_col = f"confidence_{window}d"

                n = len(window_data)
                if n < business_rules.min_snapshots_for_velocity:
                    row[col] = None
                    row[conf_col] = "insufficient_data"
                else:
                    total_sales = window_data["sales_qty"].fillna(0).sum()
                    actual_days = (
                        window_data["snapshot_date"].max()
                        - window_data["snapshot_date"].min()
                    ).days or 1
                    row[col] = round(total_sales / actual_days, 2)
                    row[conf_col] = "reliable" if n >= 20 else "limited"

            # ── Trend: compare 7d vs 30d velocity ──
            v7 = row.get("avg_daily_sales_7d")
            v30 = row.get("avg_daily_sales_30d")
            if v7 is not None and v30 is not None and v30 > 0:
                ratio = v7 / v30
                if ratio > 1.3:
                    row["velocity_trend"] = "accelerating"
                elif ratio < 0.7:
                    row["velocity_trend"] = "slowing"
                else:
                    row["velocity_trend"] = "stable"
            else:
                row["velocity_trend"] = "unknown"

            results.append(row)

        return pd.DataFrame(results), warnings

    def _compute_health(
        self,
        latest: pd.DataFrame,
        velocity: pd.DataFrame,
        as_of: date,
        suppliers: pd.DataFrame = None,
    ):
        """
        Compute item_health: days_remaining, urgency, margin, reorder predictions.

        suppliers: optional DataFrame with columns [supplier, lead_time_days].
                   When provided, lead_time is looked up per item's company.
                   Default lead time when missing: 2 days.
        """
        warnings = []
        df = latest.merge(velocity, on="item_code", how="left")

        # ── Lead time lookup ──
        DEFAULT_LEAD_TIME = 2
        if suppliers is not None and not suppliers.empty:
            supplier_lt = suppliers.set_index("supplier")["lead_time_days"].to_dict()
        else:
            supplier_lt = {}

        def lookup_lead_time(row):
            sup = row.get("company")
            if sup and sup in supplier_lt:
                return int(supplier_lt[sup])
            return DEFAULT_LEAD_TIME

        df["lead_time_days"] = df.apply(lookup_lead_time, axis=1)

        # ── Days remaining ──
        def days_remaining(row):
            stock = row.get("closing_stock") or row.get("stock", 0)
            v = row.get("avg_daily_sales_30d")
            if stock is None or pd.isna(stock):
                return None
            if stock <= 0:
                return 0
            if v is None or pd.isna(v) or v == 0:
                return None
            return round(stock / v, 1)

        df["days_remaining"] = df.apply(days_remaining, axis=1)

        # ── Reorder point ──
        # reorder_point = avg_daily_sales_30d × lead_time_days
        # When to reorder = stock at or below this value
        def reorder_point(row):
            v = row.get("avg_daily_sales_30d")
            lt = row.get("lead_time_days", DEFAULT_LEAD_TIME)
            if v is None or pd.isna(v) or v == 0:
                return None
            return round(float(v) * int(lt), 1)

        df["reorder_point"] = df.apply(reorder_point, axis=1)

        # ── Predicted stockout date ──
        def stockout_date(row):
            days = row.get("days_remaining")
            if days is None or pd.isna(days):
                return None
            try:
                return (pd.Timestamp(as_of) + pd.Timedelta(days=float(days))).date().isoformat()
            except Exception:
                return None

        df["predicted_stockout_date"] = df.apply(stockout_date, axis=1)

        # ── Urgency classification ──
        # KEY FIX: items with no velocity AND no stock are 'inactive', not critical.
        # This kills false alerts for discontinued suppliers (e.g. Aculife when
        # we've migrated to Hindustan IV fluids).
        def urgency(row):
            stock = row.get("closing_stock") or row.get("stock", 0)
            v = row.get("avg_daily_sales_30d")
            rp = row.get("reorder_point")

            try:
                stock_val = float(stock) if stock is not None and not pd.isna(stock) else 0
            except (TypeError, ValueError):
                stock_val = 0

            has_velocity = v is not None and not pd.isna(v) and v > 0

            # No movement at all → inactive or dormant
            if not has_velocity:
                if stock_val <= 0:
                    return "inactive"   # No sales, no stock — likely discontinued
                else:
                    return "dormant"    # Has stock, no demand — review separately

            # Negative stock — data issue
            if stock_val < 0:
                return "anomaly"

            # Has velocity — use reorder_point logic
            if rp is None or pd.isna(rp):
                return "unknown"

            if stock_val <= rp:
                return "critical"     # At or below reorder point — order NOW
            if stock_val <= rp * 2:
                return "watch"        # Within 2x of reorder point — monitor
            return "ok"

        df["reorder_urgency"] = df.apply(urgency, axis=1)

        # ── Margin status ──
        # Correct formula: (sale_rate - purchase_price) / purchase_price × 100
        # MRP is a regulatory ceiling, not a cost basis.
        if "purchase_price" in df.columns and "sales_price" in df.columns:
            def compute_margin(row):
                cost = row.get("purchase_price")
                sale = row.get("sales_price")
                if cost is None or pd.isna(cost) or cost <= 0:
                    return None
                if sale is None or pd.isna(sale) or sale <= 0:
                    return None
                return round((sale - cost) / cost * 100, 2)
            df["margin_pct"] = df.apply(compute_margin, axis=1)

        if "margin_pct" in df.columns:
            def margin_status(pct):
                if pct is None or pd.isna(pct):
                    return "unknown"
                if pct < 0:
                    return "loss"
                if pct <= business_rules.critical_margin_pct:
                    return "critical"
                if pct <= business_rules.watch_margin_pct:
                    return "watch"
                return "ok"
            df["margin_status"] = df["margin_pct"].apply(margin_status)

        df["computed_date"] = as_of.isoformat()

        # ── Trim to item_health schema columns only ──
        # The merge with velocity brings in avg_daily_sales_* and confidence_*
        # columns which belong on item_velocity, NOT item_health.
        health_cols = [
            "item_code", "computed_date", "closing_stock",
            "days_remaining", "reorder_urgency",
            "margin_pct", "margin_status",
            "reorder_point", "predicted_stockout_date", "lead_time_days",
            "last_supplier",
        ]
        df = df[[c for c in health_cols if c in df.columns]].copy()

        return df, warnings

    def _compute_anomalies(
        self,
        latest: pd.DataFrame,
        health: pd.DataFrame,
        snapshots: pd.DataFrame,
        as_of: date,
        item_dim: dict = None,
    ) -> pd.DataFrame:
        """
        Detect anomalies and return a flat list with item_code only.
        item_name is deliberately NOT stored here — it is fetched at query
        time via the v_anomalies view which joins to stock_items.
        This avoids stale/null names from being baked into the table.

        item_dim: dict of {code: {name, company, category, ...}} from stock_items.
                  Used for name-based filtering (slow-moving keywords) only.
        """
        if item_dim is None:
            item_dim = {}

        anomalies = []
        health_map = health.set_index("item_code").to_dict("index") if not health.empty else {}
        has_value = "value" in latest.columns

        for _, row in latest.iterrows():
            code = row.get("item_code") or row.get("code")
            health_row = health_map.get(code, {})

            # Negative stock
            stock = row.get("closing_stock") or row.get("stock", 0)
            if stock is not None and not pd.isna(stock) and stock < 0:
                anomalies.append({
                    "item_code": code,
                    "anomaly_type": "negative_stock",
                    "severity": "high",
                    "detail": f"Stock is {stock} units. Likely return entry issue.",
                    "detected_date": as_of.isoformat()
                })

            # Critical stock (velocity-based urgency)
            if health_row.get("reorder_urgency") == "critical":
                days = health_row.get("days_remaining")
                rp = health_row.get("reorder_point")
                anomalies.append({
                    "item_code": code,
                    "anomaly_type": "critical_stock",
                    "severity": "critical",
                    "detail": (
                        f"{days} days remaining at current velocity. "
                        f"Reorder point: {rp} units."
                    ),
                    "detected_date": as_of.isoformat()
                })

            # Margin erosion — selling at a loss
            if health_row.get("margin_status") == "loss":
                pct = health_row.get("margin_pct")
                anomalies.append({
                    "item_code": code,
                    "anomaly_type": "margin_erosion",
                    "severity": "high",
                    "detail": f"Selling below purchase cost. Margin: {pct:.1f}%.",
                    "detected_date": as_of.isoformat()
                })
            elif health_row.get("margin_status") == "critical":
                pct = health_row.get("margin_pct")
                anomalies.append({
                    "item_code": code,
                    "anomaly_type": "margin_erosion",
                    "severity": "medium",
                    "detail": (
                        f"Margin at {pct:.1f}% — below critical threshold of "
                        f"{business_rules.critical_margin_pct}%."
                    ),
                    "detected_date": as_of.isoformat()
                })

        # Dead stock: items with no sales in 90 days
        # Use item_dim for name-based slow-moving keyword exclusions.
        # If item_dim is empty (no stock_items data passed in), skip keyword filter
        # and rely only on price threshold.
        SLOW_MOVING_KEYWORDS = {
            "GLOVE", "DISPO", "SYRINGE", "BANDAGE", "GAUZE", "GAUGE",
            "COTTON", "CREPE", "PLASTER", "CATHETER", "TUBE", "MASK",
            "GOWN", "DRAPE", "APRON", "SHOE COVER", "STOCKING",
            "THERMOMETER", "OXIMETER", "MONITOR", "STAND", "TRAY",
            "BOWL", "FORCEP", "SCISSOR", "CLAMP", "RETRACTOR", "SPECULUM",
            "ANALYZER", "GLUCOMETER", "STRIPS", "LANCET", "CANNULA",
        }

        def is_slow_moving(code: str) -> bool:
            dim = item_dim.get(code, {})
            name_upper = (dim.get("name") or "").upper()
            if any(kw in name_upper for kw in SLOW_MOVING_KEYWORDS):
                return True
            # High-value items are typically equipment — slow by design
            item_row = latest[latest["item_code"] == code]
            if not item_row.empty:
                pp = item_row["purchase_price"].iloc[0] if "purchase_price" in item_row else None
                try:
                    if pp and float(pp) > 5000:
                        return True
                except (TypeError, ValueError):
                    pass
            return False

        cutoff_90 = pd.Timestamp(as_of) - pd.Timedelta(days=business_rules.dead_stock_days)
        recent_sales = (
            snapshots[snapshots["snapshot_date"] >= cutoff_90]
            .groupby("item_code")["sales_qty"]
            .sum()
        )

        for code, total_sales in recent_sales.items():
            if total_sales == 0:
                if is_slow_moving(code):
                    continue

                item_row = latest[latest["item_code"] == code]
                val = 0
                if not item_row.empty and has_value:
                    val = item_row["value"].iloc[0] or 0

                anomalies.append({
                    "item_code": code,
                    "anomaly_type": "dead_stock",
                    "severity": "medium",
                    "detail": (
                        f"No sales in {business_rules.dead_stock_days} days. "
                        f"Locked capital: ₹{val:,.0f}." if val else
                        f"No sales in {business_rules.dead_stock_days} days."
                    ),
                    "detected_date": as_of.isoformat()
                })

        # Output: item_code only. item_name resolved at query time via v_anomalies view.
        cols = ["item_code", "anomaly_type", "severity", "detail", "detected_date"]
        return pd.DataFrame(anomalies, columns=cols) if anomalies else pd.DataFrame(columns=cols)

    def _compute_supplier_intelligence(self, snapshots: pd.DataFrame):
        """
        Compute per-supplier rate trends.
        Requires 'company' and 'purchase_price' columns in snapshots.
        """
        warnings = []

        if "company" not in snapshots.columns or "purchase_price" not in snapshots.columns:
            warnings.append("Cannot compute supplier intelligence: missing company or purchase_price columns")
            return pd.DataFrame(), warnings

        snapshots = snapshots.dropna(subset=["company"])
        results = []

        for supplier, group in snapshots.groupby("company"):
            group = group.sort_values("snapshot_date")
            recent_30 = group[
                group["snapshot_date"] >= group["snapshot_date"].max() - pd.Timedelta(days=30)
            ]
            recent_90 = group[
                group["snapshot_date"] >= group["snapshot_date"].max() - pd.Timedelta(days=90)
            ]

            avg_30 = recent_30["purchase_price"].mean() if not recent_30.empty else None
            avg_90 = recent_90["purchase_price"].mean() if not recent_90.empty else None

            trend = "unknown"
            if avg_30 and avg_90 and avg_90 > 0:
                change = (avg_30 - avg_90) / avg_90
                if change > 0.05:
                    trend = "increasing"
                elif change < -0.05:
                    trend = "decreasing"
                else:
                    trend = "stable"

            results.append({
                "supplier": supplier,
                "avg_rate_30d": round(avg_30, 2) if avg_30 else None,
                "avg_rate_90d": round(avg_90, 2) if avg_90 else None,
                "rate_trend": trend,
                "items_supplied": group["item_code"].nunique(),
                "computed_date": date.today().isoformat()
            })

        return pd.DataFrame(results), warnings