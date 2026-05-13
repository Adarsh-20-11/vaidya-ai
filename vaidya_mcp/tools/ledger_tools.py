"""
tools/ledger_tools.py

MCP tools for outstanding ledger intelligence.
Answers: who owes us, who do we owe, and how does that intersect
with stock and supplier relationships.
"""

import logging
from datetime import date
from typing import Optional

from core.database import get_supabase

logger = logging.getLogger(__name__)


def get_customer_outstanding(
    min_amount: float = 0,
    limit: int = 20,
) -> dict:
    """
    Get customers with outstanding receivables — money they owe us.

    Use this tool when:
    - Owner asks who has pending payments
    - Before giving a large order to a customer (credit check)
    - Generating weekly collections review
    - Owner asks "kaun paisa nahi diya abhi tak?"

    Args:
        min_amount: Only show customers owing more than this (default 0 = all)
        limit:      Max customers to return, highest outstanding first

    Returns customers sorted by amount_receivable descending.
    """
    try:
        client = get_supabase()

        q = client.table("v_customer_outstanding")\
            .select("party_name, city, amount_receivable, as_of_date")\
            .order("amount_receivable", desc=True)\
            .limit(limit)

        if min_amount > 0:
            q = q.gte("amount_receivable", min_amount)

        result = q.execute()
        customers = result.data or []

        total = sum(c.get("amount_receivable") or 0 for c in customers)

        return {
            "customers": customers,
            "count": len(customers),
            "total_shown": round(total, 2),
            "as_of": customers[0].get("as_of_date") if customers else None,
            "note": (
                "Sorted by outstanding amount, highest first. "
                "Cross-reference with sales history to understand relationship."
            )
        }

    except Exception as e:
        logger.error(f"get_customer_outstanding failed: {e}")
        return {"error": str(e), "customers": []}


def get_vendor_payables(
    min_amount: float = 0,
    limit: int = 20,
) -> dict:
    """
    Get vendors we owe money to — our outstanding payables.

    Use this tool when:
    - Owner asks which suppliers need to be paid
    - Checking if we can place a new order (do we already owe them a lot?)
    - Weekly payment planning
    - Owner asks "kisko paisa dena hai?"

    Cross-reference with stock_status: if a vendor has critical stock items
    AND high payables, that's a priority payment to maintain supply.

    Args:
        min_amount: Only show vendors owed more than this (default 0 = all)
        limit:      Max vendors to return, highest payable first
    """
    try:
        client = get_supabase()

        q = client.table("v_vendor_outstanding")\
            .select("party_name, city, group_name, amount_payable, as_of_date")\
            .order("amount_payable", desc=True)\
            .limit(limit)

        if min_amount > 0:
            q = q.gte("amount_payable", min_amount)

        result = q.execute()
        vendors = result.data or []

        total = sum(v.get("amount_payable") or 0 for v in vendors)

        return {
            "vendors": vendors,
            "count": len(vendors),
            "total_payable": round(total, 2),
            "as_of": vendors[0].get("as_of_date") if vendors else None,
            "note": (
                "Sorted by payable amount, highest first. "
                "If a vendor also has items in critical stock, "
                "prioritise their payment to avoid supply disruption."
            )
        }

    except Exception as e:
        logger.error(f"get_vendor_payables failed: {e}")
        return {"error": str(e), "vendors": []}


def get_party_balance(party_name: str) -> dict:
    """
    Get the outstanding balance for a specific party (customer or vendor).

    Use this tool when:
    - Owner asks about a specific customer or vendor's balance
    - Before approving a large credit order
    - Checking payment status of a specific hospital or clinic

    Args:
        party_name: Partial or full party name (case-insensitive)
    """
    try:
        client = get_supabase()

        result = client.table("party_outstanding")\
            .select("*")\
            .ilike("party_name", f"%{party_name}%")\
            .execute()

        parties = result.data or []

        if not parties:
            return {
                "found": False,
                "query": party_name,
                "note": "No party found with this name in the ledger."
            }

        return {
            "found": True,
            "parties": parties,
            "count": len(parties),
        }

    except Exception as e:
        logger.error(f"get_party_balance failed: {e}")
        return {"error": str(e)}