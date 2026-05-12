"""
tools/customer_tools.py

MCP tools for customer-tier intelligence — rate variance across customers,
discount patterns, and per-customer pricing analysis.

These tools answer questions like:
  - "Which customers consistently get the biggest discounts?"
  - "Compare the rates we charge ABC Hospital vs XYZ Clinic for Amikacin"
  - "Which items have the highest rate variance across customers?"
  - "Show me items sold below cost"

Powered by the `discount_pct` column on sales_entries and the natural rate
variation that occurs when different customers pay different prices for
the same item.
"""

import logging
from typing import Optional

from core.database import get_supabase

logger = logging.getLogger(__name__)


def get_customer_discounts(
    customer_name: Optional[str] = None,
    min_discount_pct: float = 1.0,
    limit: int = 20,
) -> dict:
    """
    Get customers who receive discounts, ranked by average discount %.

    Use this tool when:
    - Owner asks about pricing tiers or which customers get the best rates
    - Investigating if a customer is getting too good a deal
    - Comparing customer profitability
    - Owner asks "who pays full price and who gets discounts?"

    Args:
        customer_name:   Filter to a specific customer (partial match ok)
        min_discount_pct: Only include customers with avg discount above this %
        limit:           Max customers to return

    Returns:
        customers: List of {customer_name, transaction_count, avg_discount_pct,
                            max_discount_pct, total_billed, total_full_price}
        summary:   Aggregate stats across all returned customers
    """
    try:
        client = get_supabase()

        # Pull discount data — only retail sales (exclude wholesale, returns)
        q = client.table("sales_entries").select(
            "customer_name, qty, rate, amount, discount_pct"
        ).eq("category", "retail")

        if customer_name:
            q = q.ilike("customer_name", f"%{customer_name}%")

        result = q.execute()
        rows = result.data or []

        if not rows:
            return {"customers": [], "note": "No matching sales data found"}

        # Aggregate per customer in Python (Supabase RPC would be faster but
        # this keeps the tool self-contained)
        from collections import defaultdict
        per_customer = defaultdict(lambda: {
            "txn_count": 0, "discounts": [], "total_billed": 0.0,
            "total_full_price": 0.0,
        })

        for row in rows:
            cust = row.get("customer_name")
            if not cust:
                continue
            disc = row.get("discount_pct")
            qty = row.get("qty") or 0
            rate = row.get("rate") or 0
            amount = row.get("amount") or 0

            agg = per_customer[cust]
            agg["txn_count"] += 1
            agg["total_billed"] += float(amount)
            agg["total_full_price"] += float(qty) * float(rate)
            if disc is not None:
                agg["discounts"].append(float(disc))

        customers = []
        for cust, agg in per_customer.items():
            if not agg["discounts"]:
                continue
            avg_disc = sum(agg["discounts"]) / len(agg["discounts"])
            if avg_disc < min_discount_pct:
                continue
            customers.append({
                "customer_name": cust,
                "transaction_count": agg["txn_count"],
                "avg_discount_pct": round(avg_disc, 2),
                "max_discount_pct": round(max(agg["discounts"]), 2),
                "total_billed": round(agg["total_billed"], 2),
                "total_full_price": round(agg["total_full_price"], 2),
                "total_discount_value": round(
                    agg["total_full_price"] - agg["total_billed"], 2
                ),
            })

        customers.sort(key=lambda c: c["avg_discount_pct"], reverse=True)
        customers = customers[:limit]

        return {
            "customers": customers,
            "count": len(customers),
            "filter": {
                "customer_name": customer_name,
                "min_discount_pct": min_discount_pct,
            },
            "interpretation": (
                "Positive discount = customer paid LESS than rate × qty. "
                "Sort is by avg discount % descending — top entries are "
                "your most price-favoured customers."
            ),
        }

    except Exception as e:
        logger.error(f"get_customer_discounts failed: {e}")
        return {"error": str(e), "customers": []}


def get_item_rate_variance(
    item_code: Optional[str] = None,
    min_customers: int = 3,
    limit: int = 20,
) -> dict:
    """
    Find items sold at different rates to different customers.

    Use this tool when:
    - Owner asks "are we pricing consistently?"
    - Looking for items where rate negotiation is happening
    - Identifying products with unclear pricing strategy
    - Owner asks about per-product price variance

    Args:
        item_code:     Filter to a specific item (exact code)
        min_customers: Only include items sold to at least N distinct customers
        limit:         Max items to return

    Returns:
        items: List of {item_code, item_name, unique_customers,
                       min_rate, max_rate, avg_rate, spread_pct}
    """
    try:
        client = get_supabase()

        q = client.table("sales_entries").select(
            "item_code, item_name, customer_name, rate"
        ).eq("category", "retail")

        if item_code:
            q = q.eq("item_code", item_code)

        result = q.execute()
        rows = result.data or []

        if not rows:
            return {"items": [], "note": "No matching sales data found"}

        from collections import defaultdict
        per_item = defaultdict(lambda: {
            "name": "", "customers": set(), "rates": [],
        })

        for row in rows:
            code = row.get("item_code")
            if not code:
                continue
            rate = row.get("rate")
            if rate is None or float(rate) <= 0:
                continue
            agg = per_item[code]
            agg["name"] = row.get("item_name", "")
            agg["customers"].add(row.get("customer_name", ""))
            agg["rates"].append(float(rate))

        items = []
        for code, agg in per_item.items():
            n_cust = len(agg["customers"])
            if n_cust < min_customers:
                continue
            rates = agg["rates"]
            min_r, max_r = min(rates), max(rates)
            avg_r = sum(rates) / len(rates)
            spread_pct = ((max_r - min_r) / min_r * 100) if min_r > 0 else 0
            items.append({
                "item_code": code,
                "item_name": agg["name"],
                "unique_customers": n_cust,
                "transactions": len(rates),
                "min_rate": round(min_r, 2),
                "max_rate": round(max_r, 2),
                "avg_rate": round(avg_r, 2),
                "spread_pct": round(spread_pct, 2),
            })

        items.sort(key=lambda i: i["spread_pct"], reverse=True)
        items = items[:limit]

        return {
            "items": items,
            "count": len(items),
            "interpretation": (
                "spread_pct = (max_rate - min_rate) / min_rate × 100. "
                "Items at top of list have the biggest rate variance — "
                "indicating customer-tier pricing or inconsistent negotiation."
            ),
        }

    except Exception as e:
        logger.error(f"get_item_rate_variance failed: {e}")
        return {"error": str(e), "items": []}


def compare_customer_rates(
    item_code: str,
    customer_names: Optional[list] = None,
) -> dict:
    """
    Compare what different customers pay for the same item.

    Use this tool when:
    - Owner asks "what does customer X pay for item Y vs other customers?"
    - Investigating fairness of pricing
    - Preparing for a price negotiation with a customer
    - Comparing 2-3 specific customers head-to-head

    Args:
        item_code:      The item to compare (use search_item() to find code)
        customer_names: Optional list of customer name fragments to include.
                       If None, returns ALL customers for the item.

    Returns:
        comparison: List of {customer_name, transactions, avg_rate,
                            min_rate, max_rate, total_qty, total_value}
    """
    try:
        client = get_supabase()

        q = client.table("sales_entries").select(
            "customer_name, qty, rate, amount, date"
        ).eq("item_code", item_code).eq("category", "retail")

        result = q.execute()
        rows = result.data or []

        if not rows:
            return {
                "comparison": [],
                "item_code": item_code,
                "note": f"No sales found for item {item_code}",
            }

        # Filter by customer names if provided
        if customer_names:
            filters = [n.upper() for n in customer_names]
            rows = [
                r for r in rows
                if any(f in (r.get("customer_name", "") or "").upper() for f in filters)
            ]

        from collections import defaultdict
        per_customer = defaultdict(lambda: {
            "rates": [], "total_qty": 0.0, "total_value": 0.0,
        })

        for row in rows:
            cust = row.get("customer_name")
            if not cust:
                continue
            rate = row.get("rate")
            if rate is None:
                continue
            agg = per_customer[cust]
            agg["rates"].append(float(rate))
            agg["total_qty"] += float(row.get("qty") or 0)
            agg["total_value"] += float(row.get("amount") or 0)

        comparison = []
        for cust, agg in per_customer.items():
            rates = agg["rates"]
            comparison.append({
                "customer_name": cust,
                "transactions": len(rates),
                "min_rate": round(min(rates), 2),
                "max_rate": round(max(rates), 2),
                "avg_rate": round(sum(rates) / len(rates), 2),
                "total_qty": round(agg["total_qty"], 2),
                "total_value": round(agg["total_value"], 2),
            })

        comparison.sort(key=lambda c: c["avg_rate"])

        # Get item name for context
        item_meta = client.table("stock_items").select("name, mrp").eq(
            "code", item_code
        ).execute()
        item_name = item_meta.data[0].get("name") if item_meta.data else item_code
        item_mrp = item_meta.data[0].get("mrp") if item_meta.data else None

        return {
            "item_code": item_code,
            "item_name": item_name,
            "mrp": item_mrp,
            "comparison": comparison,
            "interpretation": (
                "Sorted by avg_rate ascending — customers paying the lowest "
                "rates appear first. Compare against MRP to see how much "
                "discount each customer is getting."
            ),
        }

    except Exception as e:
        logger.error(f"compare_customer_rates failed: {e}")
        return {"error": str(e)}


def get_below_cost_sales(limit: int = 20) -> dict:
    """
    Find sales where the customer paid less than the item's purchase price.
    This is genuinely concerning — it means the business lost money on these.

    Use this tool when:
    - Owner asks if anything is being sold at a loss
    - Investigating margin erosion
    - Reviewing customer pricing for unprofitable accounts

    Returns:
        below_cost_sales: List of unprofitable transactions sorted by loss amount
    """
    try:
        client = get_supabase()

        # Join sales with latest snapshot to get purchase_price per item
        # Simpler approach: pull both, join in Python
        sales = client.table("sales_entries").select(
            "date, customer_name, item_code, item_name, qty, rate, amount, mrp"
        ).eq("category", "retail").limit(2000).execute()

        if not sales.data:
            return {"below_cost_sales": [], "note": "No sales data found"}

        # Map item_code → latest purchase_price
        items = client.table("stock_snapshots").select(
            "item_code, purchase_price, snapshot_date"
        ).execute()

        cost_map = {}
        for r in items.data or []:
            code = r.get("item_code")
            cost = r.get("purchase_price")
            date_str = r.get("snapshot_date", "")
            if code and cost is not None:
                # Keep latest per item
                if code not in cost_map or date_str > cost_map[code]["date"]:
                    cost_map[code] = {"cost": float(cost), "date": date_str}

        below = []
        for s in sales.data:
            code = s.get("item_code")
            rate = s.get("rate")
            if code not in cost_map or rate is None:
                continue
            cost = cost_map[code]["cost"]
            if cost <= 0 or float(rate) >= cost:
                continue
            loss_per_unit = cost - float(rate)
            total_loss = loss_per_unit * float(s.get("qty") or 0)
            below.append({
                "date": s.get("date"),
                "customer_name": s.get("customer_name"),
                "item_code": code,
                "item_name": s.get("item_name"),
                "qty": s.get("qty"),
                "sold_at": float(rate),
                "purchase_cost": cost,
                "loss_per_unit": round(loss_per_unit, 2),
                "total_loss": round(total_loss, 2),
            })

        below.sort(key=lambda x: x["total_loss"], reverse=True)
        below = below[:limit]

        total = sum(b["total_loss"] for b in below)

        return {
            "below_cost_sales": below,
            "count": len(below),
            "total_loss_value": round(total, 2),
            "interpretation": (
                "These transactions sold below the item's purchase cost. "
                "Either there's a data issue (wrong cost) or genuine pricing "
                "errors. Investigate top entries first."
            ),
        }

    except Exception as e:
        logger.error(f"get_below_cost_sales failed: {e}")
        return {"error": str(e), "below_cost_sales": []}