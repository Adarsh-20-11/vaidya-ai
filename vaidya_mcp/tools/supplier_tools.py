"""
tools/supplier_tools.py

MCP tools for supplier intelligence, margin alerts, and anomaly queries.
"""

import logging
from typing import Optional
from datetime import date

from core.database import get_supabase

logger = logging.getLogger(__name__)


def get_supplier_info(
    item_code: Optional[str] = None,
    supplier_name: Optional[str] = None
) -> dict:
    """
    Get supplier information and rate trends.

    Use this tool when:
    - You need to know who supplies a specific item
    - Owner asks about a vendor's pricing history
    - Deciding whether to negotiate with a supplier
    - Drafting a vendor order message

    Provide either item_code OR supplier_name (or both to cross-reference).

    Args:
        item_code:     Get supplier for this specific item
        supplier_name: Get all items and rate trend for this supplier

    Returns dict with:
        supplier:    Supplier name
        last_rate:   Most recent purchase rate
        rate_trend:  'increasing' | 'stable' | 'decreasing'
        items:       Items supplied by this vendor
        rate_30d:    Average rate last 30 days
        rate_90d:    Average rate last 90 days
    """
    try:
        client = get_supabase()
        response = {}

        if item_code:
            # Get supplier from stock_items
            item_result = client.table("stock_items")\
                .select("code, name, company, mrp")\
                .eq("code", item_code)\
                .execute()

            if item_result.data:
                item = item_result.data[0]
                response["item_code"] = item_code
                response["item_name"] = item.get("name")
                response["supplier"] = item.get("company")
                supplier_name = item.get("company") or supplier_name

            # Get last purchase rate from snapshots
            rate_result = client.table("stock_snapshots")\
                .select("snapshot_date, purchase_price")\
                .eq("item_code", item_code)\
                .order("snapshot_date", desc=True)\
                .limit(1)\
                .execute()

            if rate_result.data:
                response["last_rate"] = rate_result.data[0].get("purchase_price")
                response["last_rate_date"] = rate_result.data[0].get("snapshot_date")

        if supplier_name:
            # Get supplier intelligence
            sup_result = client.table("supplier_intelligence")\
                .select("*")\
                .ilike("supplier", f"%{supplier_name}%")\
                .order("computed_date", desc=True)\
                .limit(1)\
                .execute()

            if sup_result.data:
                sup = sup_result.data[0]
                response.update({
                    "rate_30d": sup.get("avg_rate_30d"),
                    "rate_90d": sup.get("avg_rate_90d"),
                    "rate_trend": sup.get("rate_trend"),
                    "items_supplied": sup.get("items_supplied"),
                })

                # Rate change context
                r30 = sup.get("avg_rate_30d")
                r90 = sup.get("avg_rate_90d")
                if r30 and r90 and r90 > 0:
                    pct_change = ((r30 - r90) / r90) * 100
                    response["rate_change_pct"] = round(pct_change, 1)
                    if pct_change > 5:
                        response["rate_alert"] = (
                            f"Rates have increased {pct_change:.1f}% "
                            f"in last 30 days vs 90-day average"
                        )

        if not response:
            return {
                "error": "No data found. Provide item_code or supplier_name.",
                "tip": "Use search_item() first to find the item_code"
            }

        return response

    except Exception as e:
        logger.error(f"get_supplier_info failed: {e}")
        return {"error": str(e)}


def get_margin_alerts(threshold_pct: float = 8.0) -> dict:
    """
    Get items where profit margin is critically low, eroding, or negative (loss).

    Margin = (sale_rate - purchase_cost) / purchase_cost × 100
    Negative margin = selling below what we paid for it.

    Use this tool when:
    - Owner asks about profitability
    - Supplier has increased rates without price revision
    - Generating weekly margin review
    - Looking for items that need price revision

    Args:
        threshold_pct: Flag items with margin below this % (default 8%)

    Returns dict with:
        loss:     Items sold below purchase cost (negative margin)
        critical: Items below 3% margin (immediate action needed)
        watch:    Items between 3-8% margin
    """
    try:
        client = get_supabase()

        # v_item_health_named has item_name from stock_items join
        health_result = client.table("v_item_health_named")\
            .select("item_code, item_name, supplier, margin_pct, margin_status")\
            .in_("margin_status", ["loss", "critical", "watch"])\
            .order("margin_pct")\
            .execute()

        health_items = health_result.data or []

        loss     = [i for i in health_items if i.get("margin_status") == "loss"]
        critical = [i for i in health_items if i.get("margin_status") == "critical"]
        watch    = [i for i in health_items if i.get("margin_status") == "watch"]

        rec = []
        if loss:
            rec.append(
                f"{len(loss)} items are being sold BELOW purchase cost — "
                "check if this is intentional (promotional pricing) or an error."
            )
        if critical:
            rec.append(
                f"{len(critical)} items have <3% margin — "
                "negotiate purchase rate or revise sale price."
            )

        return {
            "loss": loss,
            "critical": critical,
            "watch": watch,
            "loss_count": len(loss),
            "critical_count": len(critical),
            "watch_count": len(watch),
            "threshold_used": threshold_pct,
            "note": (
                "Margin = (sale_rate - purchase_cost) / purchase_cost × 100. "
                "MRP is not used in this calculation."
            ),
            "recommendation": " ".join(rec) if rec else "Margins look healthy today."
        }

    except Exception as e:
        logger.error(f"get_margin_alerts failed: {e}")
        return {"error": str(e), "loss": [], "critical": [], "watch": []}


def get_anomalies(
    severity: Optional[str] = None,
    anomaly_type: Optional[str] = None
) -> dict:
    """
    Get all flagged anomalies requiring attention.

    Use this tool when:
    - Starting the morning brief
    - Owner asks 'kya problem hai aaj?'
    - Checking if a specific type of issue exists

    Args:
        severity:     Filter by 'critical' | 'high' | 'medium' | 'low'
        anomaly_type: Filter by type:
                      'negative_stock'  — stock quantity is negative
                      'critical_stock'  — running out based on velocity
                      'margin_erosion'  — selling at a loss or near-loss
                      'dead_stock'      — no movement in 90 days
    """
    try:
        client = get_supabase()

        # v_anomalies has item_name from stock_items join — always correct
        q = client.table("v_anomalies").select(
            "item_code, item_name, supplier, anomaly_type, severity, "
            "detail, detected_date, closing_stock, days_remaining, "
            "margin_pct, predicted_stockout_date"
        )

        if severity:
            q = q.eq("severity", severity)
        if anomaly_type:
            q = q.eq("anomaly_type", anomaly_type)
        else:
            # Dead stock is too noisy in the general view — use get_dead_stock() specifically
            q = q.neq("anomaly_type", "dead_stock")

        result = q.order("severity").limit(100).execute()
        anomalies = result.data or []

        # Group by severity
        grouped: dict = {"critical": [], "high": [], "medium": [], "low": []}
        for a in anomalies:
            sev = a.get("severity", "low")
            grouped.setdefault(sev, []).append(a)

        return {
            "anomalies": grouped,
            "total": len(anomalies),
            "as_of": date.today().isoformat(),
            "summary": {k: len(v) for k, v in grouped.items()},
            "note": (
                "dead_stock excluded here — use dead_stock() tool for those. "
                "Use get_item_velocity() on critical items to understand impact."
            )
        }

    except Exception as e:
        logger.error(f"get_anomalies failed: {e}")
        return {"error": str(e), "anomalies": {}}


def draft_vendor_message(
    item_code: str,
    quantity: int,
    supplier_name: str,
    last_rate: float,
    notes: Optional[str] = None
) -> dict:
    """
    Draft a WhatsApp order message to send to a supplier.

    Use this tool when:
    - Owner confirms they want to place an order
    - You've identified a critical stock item and a known supplier
    - Owner asks to 'draft a message' or 'order banao'

    IMPORTANT: Always show this draft to the owner for confirmation
    before sending. Never send automatically.

    Args:
        item_code:     Item to order
        quantity:      Number of units to order
        supplier_name: Supplier to send the message to
        last_rate:     Last known purchase rate (for negotiation reference)
        notes:         Any special instructions

    Returns dict with:
        draft:    Ready-to-send WhatsApp message text
        warning:  Reminder that owner must confirm before sending
    """
    try:
        client = get_supabase()

        # Get item details
        item_result = client.table("stock_items")\
            .select("name, unit")\
            .eq("code", item_code)\
            .execute()

        item_name = item_result.data[0].get("name") if item_result.data else item_code
        unit = item_result.data[0].get("unit", "units") if item_result.data else "units"

        # Build the message
        message_lines = [
            f"Namaste {supplier_name} ji,",
            "",
            f"Magadh Wellness, Gaya ki taraf se order dena tha:",
            "",
            f"*Item:* {item_name}",
            f"*Quantity:* {quantity} {unit}",
            f"*Reference Rate:* ₹{last_rate:.2f} (last purchase)",
            "",
        ]

        if notes:
            message_lines.append(f"*Special Note:* {notes}")
            message_lines.append("")

        message_lines += [
            "Kripya confirm karein:",
            "1. Current rate kya hogi?",
            "2. Delivery Gaya tak kitne din mein?",
            "3. Koi scheme available hai?",
            "",
            "Dhanyawad 🙏",
            "Magadh Wellness Private Limited",
        ]

        draft = "\n".join(message_lines)

        return {
            "draft": draft,
            "item_code": item_code,
            "item_name": item_name,
            "supplier": supplier_name,
            "quantity": quantity,
            "unit": unit,
            "estimated_value": round(quantity * last_rate, 2),
            "warning": "⚠️ DRAFT ONLY — Show to owner for approval before sending.",
            "next_step": "Owner confirms → copy message → send on WhatsApp"
        }

    except Exception as e:
        logger.error(f"draft_vendor_message failed: {e}")
        return {"error": str(e)}