"""
tools/stock_tools.py

MCP tools for stock intelligence queries.
All queries go through named views (v_item_dashboard, v_anomalies,
v_item_health_named) which join to stock_items at the DB layer.
Item names are NEVER enriched in Python — that is the DB's job.
"""

import logging
from typing import Optional
from datetime import date, timedelta

from core.database import get_supabase, query_view, query

logger = logging.getLogger(__name__)


def get_stock_status(
    urgency: Optional[str] = None,
    limit: int = 20
) -> dict:
    """
    Get current stock health for items in the inventory.

    Use this tool when:
    - Owner asks what needs ordering today
    - Owner asks what is running low
    - Generating the morning brief

    Args:
        urgency: Filter by urgency level:
                 'critical' = stock at or below reorder_point (order NOW)
                 'watch'    = stock within 2x of reorder_point (monitor)
                 'ok'       = healthy stock levels
                 'dormant'  = has stock but zero recent sales (review separately)
                 'anomaly'  = negative stock (data issue)
                 None       = critical + watch only (actionable items — default)
                 Note: 'inactive' items (no sales + no stock — likely
                 discontinued) are always hidden.
        limit:  Maximum items to return (default 20)
    """
    try:
        client = get_supabase()

        q = client.table("v_item_dashboard").select(
            "code, name, default_supplier, item_category, "
            "closing_stock, days_remaining, reorder_urgency, "
            "reorder_point, predicted_stockout_date, lead_time_days, "
            "avg_daily_sales_30d, velocity_trend, margin_pct, margin_status"
        ).eq("supplier_is_active", True)

        if urgency:
            q = q.eq("reorder_urgency", urgency)
        else:
            q = q.in_("reorder_urgency", ["critical", "watch"])

        result = q.limit(limit).execute()
        items = [
            {**r,
             "item_code": r.get("code"),
             "item_name": r.get("name"),
             "supplier":  r.get("default_supplier")}
            for r in (result.data or [])
        ]

        # Summary counts across all items
        all_result = client.table("item_health").select("reorder_urgency").execute()
        summary = {
            "critical": 0, "watch": 0, "ok": 0,
            "inactive": 0, "dormant": 0, "anomaly": 0, "unknown": 0
        }
        for row in (all_result.data or []):
            urg = row.get("reorder_urgency", "unknown")
            summary[urg] = summary.get(urg, 0) + 1

        return {
            "items": items,
            "summary": summary,
            "returned": len(items),
            "as_of": date.today().isoformat(),
            "urgency_meaning": {
                "critical": "Stock at or below reorder_point — order NOW",
                "watch":    "Stock within 2x of reorder_point — monitor",
                "ok":       "Healthy stock",
                "dormant":  "Has stock but no recent sales — review separately",
                "inactive": "No stock + no sales — likely discontinued (hidden from alerts)",
                "anomaly":  "Negative stock — data issue",
            },
            "note": f"Showing {len(items)} items"
                    + (f" filtered by urgency='{urgency}'" if urgency
                       else " (critical + watch only)")
        }

    except Exception as e:
        logger.error(f"get_stock_status failed: {e}")
        return {"error": str(e), "items": [], "summary": {}}


def get_item_velocity(
    item_code: str,
    include_history: bool = False
) -> dict:
    """
    Get sales velocity and trend for a specific item.

    Use this tool when:
    - You need to know how fast an item is selling
    - Owner asks about movement of a specific product
    - Deciding reorder quantity (base it on 30d velocity)

    Args:
        item_code:       The Marg item code (e.g. 'A00010', '137')
        include_history: If True, include last 7 daily snapshots
    """
    try:
        client = get_supabase()

        # v_item_dashboard is a single join — has name, velocity, health, all together
        dash = client.table("v_item_dashboard")\
            .select("*")\
            .eq("code", item_code)\
            .execute()

        if not dash.data:
            return {"error": f"Item {item_code} not found", "item_code": item_code}

        d = dash.data[0]
        v30 = d.get("avg_daily_sales_30d")
        lt = d.get("lead_time_days") or 2

        response = {
            "item_code": item_code,
            "item_name": d.get("name", "Unknown"),
            "unit": d.get("unit"),
            "supplier": d.get("default_supplier"),
            "current_stock": d.get("closing_stock"),
            "days_remaining": d.get("days_remaining"),
            "reorder_point": d.get("reorder_point"),
            "predicted_stockout_date": d.get("predicted_stockout_date"),
            "urgency": d.get("reorder_urgency"),
            "avg_daily_sales_7d": d.get("avg_daily_sales_7d"),
            "avg_daily_sales_30d": v30,
            "velocity_trend": d.get("velocity_trend", "unknown"),
            "confidence": d.get("confidence_30d", "insufficient_data"),
            "margin_pct": d.get("margin_pct"),
            "margin_status": d.get("margin_status"),
        }

        if v30 and v30 > 0:
            response["suggested_reorder_qty"] = round(v30 * (lt + 45))
            response["suggested_reorder_basis"] = (
                f"45 days supply + {lt}d lead time at 30d velocity"
            )

        if include_history:
            cutoff = (date.today() - timedelta(days=7)).isoformat()
            history = client.table("stock_snapshots")\
                .select("snapshot_date, closing_stock, sales_qty, purchase_price")\
                .eq("item_code", item_code)\
                .gte("snapshot_date", cutoff)\
                .order("snapshot_date", desc=True)\
                .execute()
            response["history"] = history.data or []

        return response

    except Exception as e:
        logger.error(f"get_item_velocity failed for {item_code}: {e}")
        return {"error": str(e), "item_code": item_code}


def get_dead_stock(
    days: int = 90,
    limit: int = 20
) -> dict:
    """
    Get items with no sales movement — capital locked in slow inventory.

    Use this tool when:
    - Owner asks what isn't selling
    - Looking for capital to free up
    - Items to return to vendors or discount
    - Generating weekly business review

    Args:
        days:  Define 'dead' as no sales in this many days (default 90)
        limit: Max items returned, sorted by locked capital (highest first)
    """
    try:
        client = get_supabase()

        # v_anomalies has item_name from stock_items join — no enrichment needed
        result = client.table("v_anomalies")\
            .select("item_code, item_name, supplier, detail, "
                    "detected_date, closing_stock, margin_pct")\
            .eq("anomaly_type", "dead_stock")\
            .limit(limit)\
            .execute()

        items = result.data or []

        return {
            "items": items,
            "item_count": len(items),
            "definition": f"No sales in {days} days",
            "note": (
                "Surgical/equipment items are excluded — they are slow by design. "
                "Sort by locked capital (closing_stock × cost) to prioritise."
            )
        }

    except Exception as e:
        logger.error(f"get_dead_stock failed: {e}")
        return {"error": str(e), "items": []}


def search_item(query_str: str) -> dict:
    """
    Search for an item by name or code.

    Use this tool when:
    - Owner mentions a product by partial name
    - You need the item_code before calling other tools
    - Owner asks about a specific medicine or product

    Args:
        query_str: Product name (partial ok) or exact code

    Returns matching items with code, name, stock, urgency, supplier
    """
    try:
        client = get_supabase()

        result = client.table("v_item_dashboard")\
            .select("code, name, unit, item_category, closing_stock, "
                    "reorder_urgency, default_supplier, avg_daily_sales_30d, "
                    "days_remaining, predicted_stockout_date")\
            .ilike("name", f"%{query_str}%")\
            .limit(10)\
            .execute()

        items = result.data or []

        # Fallback: try exact code match
        if not items:
            result = client.table("v_item_dashboard")\
                .select("code, name, unit, item_category, closing_stock, "
                        "reorder_urgency, default_supplier, avg_daily_sales_30d, "
                        "days_remaining, predicted_stockout_date")\
                .eq("code", query_str.upper())\
                .execute()
            items = result.data or []

        return {
            "matches": items,
            "count": len(items),
            "query": query_str,
            "tip": "Use the 'code' field with other tools for precise queries"
        }

    except Exception as e:
        logger.error(f"search_item failed: {e}")
        return {"error": str(e), "matches": []}