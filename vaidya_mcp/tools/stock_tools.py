"""
tools/stock_tools.py

MCP tools for stock intelligence queries.
Each tool has one job. Descriptions are written for the AI — 
they tell it WHEN to use the tool, not just what it does.
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
    - You need to identify critical or watch-level items
    - Generating the morning brief

    Args:
        urgency: Filter by urgency level:
                 'critical' = will run out in ≤14 days
                 'watch'    = will run out in ≤30 days
                 'ok'       = healthy stock levels
                 'anomaly'  = negative stock (data issue)
                 None       = return all items sorted by urgency
        limit:  Maximum items to return (default 20)

    Returns dict with:
        items:   List of items with stock, days_remaining,
                 supplier, urgency, margin_pct
        summary: Count per urgency level
        as_of:   Date of latest data
    """
    try:
        client = get_supabase()
        q = client.table("v_item_dashboard").select("*")

        if urgency:
            q = q.eq("reorder_urgency", urgency)
        else:
            # Sort: critical first, then watch, then ok
            q = q.order("reorder_urgency")

        result = q.limit(limit).execute()
        items = result.data or []

        # Build summary counts
        all_items = client.table("item_health")\
            .select("reorder_urgency")\
            .eq("computed_date", date.today().isoformat())\
            .execute()

        summary = {"critical": 0, "watch": 0, "ok": 0, "anomaly": 0, "unknown": 0}
        for row in (all_items.data or []):
            urg = row.get("reorder_urgency", "unknown")
            summary[urg] = summary.get(urg, 0) + 1

        return {
            "items": items,
            "summary": summary,
            "returned": len(items),
            "as_of": date.today().isoformat(),
            "note": f"Showing {len(items)} items"
                    + (f" filtered by urgency='{urgency}'" if urgency else "")
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
    - Calculating days of stock remaining for a specific item
    - Owner asks about movement of a specific product
    - Deciding reorder quantity (base it on 30d velocity)

    Args:
        item_code:       The Marg item code (e.g. 'A00010', '137')
        include_history: If True, include last 7 daily snapshots

    Returns dict with:
        velocity:   avg daily sales over 7d, 30d, 90d windows
        trend:      'accelerating' | 'stable' | 'slowing' | 'unknown'
        confidence: Data reliability indicator
        days_remaining: Estimated days of stock at 30d velocity
        history:    Last 7 snapshots (if include_history=True)
    """
    try:
        client = get_supabase()

        # Get velocity
        vel_result = client.table("item_velocity")\
            .select("*")\
            .eq("item_code", item_code)\
            .execute()

        # Get current health
        health_result = client.table("item_health")\
            .select("*")\
            .eq("item_code", item_code)\
            .eq("computed_date", date.today().isoformat())\
            .execute()

        # Get item master
        item_result = client.table("stock_items")\
            .select("name, company, mrp, unit")\
            .eq("code", item_code)\
            .execute()

        velocity = vel_result.data[0] if vel_result.data else {}
        health = health_result.data[0] if health_result.data else {}
        item = item_result.data[0] if item_result.data else {}

        response = {
            "item_code": item_code,
            "item_name": item.get("name", "Unknown"),
            "unit": item.get("unit"),
            "supplier": item.get("company"),
            "current_stock": health.get("closing_stock"),
            "days_remaining": health.get("days_remaining"),
            "urgency": health.get("reorder_urgency"),
            "avg_daily_sales_7d": velocity.get("avg_daily_sales_7d"),
            "avg_daily_sales_30d": velocity.get("avg_daily_sales_30d"),
            "avg_daily_sales_90d": velocity.get("avg_daily_sales_90d"),
            "velocity_trend": velocity.get("velocity_trend", "unknown"),
            "confidence": velocity.get("confidence_30d", "insufficient_data"),
        }

        # Suggested reorder quantity = 45 days of supply
        v30 = velocity.get("avg_daily_sales_30d")
        if v30 and v30 > 0:
            response["suggested_reorder_qty"] = round(v30 * 45)
            response["suggested_reorder_basis"] = "45 days supply at 30d velocity"

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
    - You want to identify capital that could be freed up
    - Looking for items to return to vendors or discount
    - Generating weekly business review

    Args:
        days:  Define 'dead' as no sales in this many days (default 90)
        limit: Maximum items to return, sorted by locked value (highest first)

    Returns dict with:
        items:         List of dead stock items with locked value
        total_value:   Total capital locked in dead stock
        item_count:    Number of dead stock items
    """
    try:
        client = get_supabase()

        # Get anomalies of type dead_stock
        result = client.table("anomalies_today")\
            .select("*")\
            .eq("anomaly_type", "dead_stock")\
            .eq("detected_date", date.today().isoformat())\
            .order("severity")\
            .limit(limit)\
            .execute()

        items = result.data or []

        # Try to enrich with value data from stock_items
        enriched = []
        for item in items:
            code = item.get("item_code")
            if code:
                stock_data = client.table("v_item_dashboard")\
                    .select("closing_stock, mrp, default_supplier")\
                    .eq("code", code)\
                    .execute()
                if stock_data.data:
                    item.update(stock_data.data[0])
            enriched.append(item)

        return {
            "items": enriched,
            "item_count": len(enriched),
            "definition": f"No sales in {days} days",
            "note": "Sort by locked capital to prioritise which to address first"
        }

    except Exception as e:
        logger.error(f"get_dead_stock failed: {e}")
        return {"error": str(e), "items": []}


def search_item(query_str: str) -> dict:
    """
    Search for an item by name or code.

    Use this tool when:
    - Owner mentions a product by partial name
    - You need to find the item_code for a product before calling other tools
    - Owner asks about a specific medicine or product

    Args:
        query_str: Product name (partial ok) or exact code

    Returns dict with matching items (code, name, unit, current_stock, supplier)
    """
    try:
        client = get_supabase()

        # Try code match first
        result = client.table("v_item_dashboard")\
            .select("code, name, unit, closing_stock, reorder_urgency, default_supplier")\
            .ilike("name", f"%{query_str}%")\
            .limit(10)\
            .execute()

        items = result.data or []

        # Also try exact code
        if not items:
            result = client.table("v_item_dashboard")\
                .select("code, name, unit, closing_stock, reorder_urgency, default_supplier")\
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
