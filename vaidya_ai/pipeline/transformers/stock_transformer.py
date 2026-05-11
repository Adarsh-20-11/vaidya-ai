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
        as_of_date: Optional[date] = None
    ) -> TransformResult:
        """
        Main entry point.
        snapshots must have columns:
          item_code, snapshot_date, closing_stock, sales_qty, purchase_price,
          mrp, company, margin_pct
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
        health_df, health_warnings = self._compute_health(latest, velocity_df, as_of)
        warnings.extend(health_warnings)
        tables["item_health"] = health_df

        # ── Anomalies ──
        anomalies_df = self._compute_anomalies(latest, health_df, snapshots, as_of)
        tables["anomalies_today"] = anomalies_df

        # ── Supplier intelligence ──
        supplier_df, sup_warnings = self._compute_supplier_intelligence(snapshots)
        warnings.extend(sup_warnings)
        tables["supplier_intelligence"] = supplier_df

        return TransformResult(
            success=True,
            tables=tables,
            errors=errors,
            warnings=warnings
        )

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
        as_of: date
    ):
        """
        Compute item_health: days_remaining, urgency, margin status.
        """
        warnings = []
        df = latest.merge(velocity, on="item_code", how="left")

        # ── Days of stock remaining ──
        def days_remaining(row):
            stock = row.get("closing_stock") or row.get("stock", 0)
            velocity_30d = row.get("avg_daily_sales_30d")
            if stock is None or pd.isna(stock):
                return None
            if stock <= 0:
                return 0
            if velocity_30d is None or pd.isna(velocity_30d) or velocity_30d == 0:
                return None  # Can't compute without velocity
            return round(stock / velocity_30d, 1)

        df["days_remaining"] = df.apply(days_remaining, axis=1)

        # ── Urgency classification ──
        def urgency(row):
            days = row["days_remaining"]
            stock = row.get("closing_stock") or row.get("stock", 0)
            if stock is not None and stock < 0:
                return "anomaly"  # Negative stock — data issue
            if days is None:
                return "unknown"
            if days <= business_rules.critical_days_remaining:
                return "critical"
            if days <= business_rules.watch_days_remaining:
                return "watch"
            return "ok"

        df["reorder_urgency"] = df.apply(urgency, axis=1)

        # ── Margin status ──
        if "margin_pct" in df.columns:
            def margin_status(pct):
                if pct is None or pd.isna(pct):
                    return "unknown"
                if pct <= business_rules.critical_margin_pct:
                    return "critical"
                if pct <= business_rules.watch_margin_pct:
                    return "watch"
                return "ok"
            df["margin_status"] = df["margin_pct"].apply(margin_status)

        df["computed_date"] = as_of.isoformat()
        return df, warnings

    def _compute_anomalies(
        self,
        latest: pd.DataFrame,
        health: pd.DataFrame,
        snapshots: pd.DataFrame,
        as_of: date
    ) -> pd.DataFrame:
        """
        Detect anomalies and return a flat list with type + severity.
        """
        anomalies = []

        health_map = health.set_index("item_code").to_dict("index") if not health.empty else {}

        # Detect which optional columns are present in `latest`
        has_name = "name" in latest.columns
        has_value = "value" in latest.columns

        for _, row in latest.iterrows():
            code = row.get("item_code") or row.get("code")
            name = row.get("name", "") if has_name else ""
            health_row = health_map.get(code, {})

            # Negative stock
            stock = row.get("closing_stock") or row.get("stock", 0)
            if stock is not None and not pd.isna(stock) and stock < 0:
                anomalies.append({
                    "item_code": code, "item_name": name,
                    "anomaly_type": "negative_stock",
                    "severity": "high",
                    "detail": f"Stock is {stock} units. Likely return entry issue.",
                    "detected_date": as_of.isoformat()
                })

            # Critical stock
            if health_row.get("reorder_urgency") == "critical":
                days = health_row.get("days_remaining")
                anomalies.append({
                    "item_code": code, "item_name": name,
                    "anomaly_type": "critical_stock",
                    "severity": "critical",
                    "detail": f"{days} days of stock remaining at current velocity.",
                    "detected_date": as_of.isoformat()
                })

            # Margin erosion
            if health_row.get("margin_status") == "critical":
                pct = health_row.get("margin_pct")
                anomalies.append({
                    "item_code": code, "item_name": name,
                    "anomaly_type": "margin_erosion",
                    "severity": "high",
                    "detail": f"Margin at {pct:.1f}% — below critical threshold of "
                              f"{business_rules.critical_margin_pct}%.",
                    "detected_date": as_of.isoformat()
                })

        # Dead stock: items with no sales in 90 days
        cutoff_90 = pd.Timestamp(as_of) - pd.Timedelta(days=business_rules.dead_stock_days)
        recent_sales = (
            snapshots[snapshots["snapshot_date"] >= cutoff_90]
            .groupby("item_code")["sales_qty"]
            .sum()
        )

        for code, total_sales in recent_sales.items():
            if total_sales == 0:
                # Look up item info if available
                item_rows = latest[latest["item_code"] == code] if "item_code" in latest.columns else latest[latest.get("code", pd.Series()) == code]
                name = ""
                val = 0
                if not item_rows.empty:
                    if has_name:
                        name = item_rows["name"].iloc[0]
                    if has_value:
                        val = item_rows["value"].iloc[0] or 0
                anomalies.append({
                    "item_code": code, "item_name": name,
                    "anomaly_type": "dead_stock",
                    "severity": "medium",
                    "detail": f"No sales in {business_rules.dead_stock_days} days. "
                              f"Locked capital: ₹{val:,.0f}" if val else
                              f"No sales in {business_rules.dead_stock_days} days.",
                    "detected_date": as_of.isoformat()
                })

        return pd.DataFrame(anomalies) if anomalies else pd.DataFrame(
            columns=["item_code", "item_name", "anomaly_type",
                     "severity", "detail", "detected_date"]
        )

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
