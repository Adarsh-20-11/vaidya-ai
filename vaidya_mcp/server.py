"""
server.py

Vaidya-AI MCP Server — main entry point.
Run with: mcp dev server.py

For Claude Desktop, add to claude_desktop_config.json:
{
  "mcpServers": {
    "vaidya-ai": {
      "command": "python",
      "args": ["/path/to/vaidya_mcp/server.py"],
      "env": {
        "SUPABASE_URL": "...",
        "SUPABASE_KEY": "..."
      }
    }
  }
}

TOOL REGISTRY:
  Stock:    get_stock_status, get_item_velocity, get_dead_stock, search_item
  Supplier: get_supplier_info, get_margin_alerts, draft_vendor_message
  Anomaly:  get_anomalies
  Ledger:   get_party_outstanding (Phase D)
  GeM:      get_gem_tenders (Phase E)

RESOURCE REGISTRY:
  vaidya://brief/today
  vaidya://pipeline/status
  vaidya://schema/summary
"""

import logging
import os
import sys
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr   # MCP uses stdout for protocol — logs must go to stderr
)

logger = logging.getLogger("vaidya_mcp")

# ── Import FastMCP ──
try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print(
        "ERROR: mcp package not installed.\n"
        "Run: pip install 'mcp[cli]'",
        file=sys.stderr
    )
    sys.exit(1)

# ── Import tools ──
from tools.stock_tools import (
    get_stock_status,
    get_item_velocity,
    get_dead_stock,
    search_item,
)
from tools.supplier_tools import (
    get_supplier_info,
    get_margin_alerts,
    get_anomalies,
    draft_vendor_message,
)
from resources.brief_resource import (
    get_daily_brief_content,
    get_pipeline_status_content,
    get_schema_summary_content,
)

# ── Initialise server ──
mcp = FastMCP(
    name="Vaidya-AI",
    instructions="""
You are Vaidya-AI, business intelligence assistant for Magadh Wellness 
Private Limited, a pharma and surgical equipment distributor in Gaya, Bihar.

AVAILABLE TOOLS (use in this order for best results):
1. get_anomalies()         — Start here for morning brief or 'what's wrong today'
2. get_stock_status()      — Stock health overview, filter by urgency
3. get_item_velocity()     — Deep dive on a specific item
4. get_supplier_info()     — Supplier rates and trends
5. get_margin_alerts()     — Items with eroding profitability
6. get_dead_stock()        — Capital locked in slow-moving inventory
7. search_item()           — Find item code from partial name
8. draft_vendor_message()  — Generate order message (owner must confirm)

RESOURCES:
- vaidya://brief/today     — Today's pre-generated brief
- vaidya://pipeline/status — Data freshness and pipeline health
- vaidya://schema/summary  — Database schema (for advanced queries)

Always check pipeline/status if data seems stale or incomplete.
""".strip()
)


# ────────────────────────────────────────────
# TOOLS
# ────────────────────────────────────────────

@mcp.tool()
def stock_status(urgency: str = None, limit: int = 20) -> dict:
    """
    Get current stock health. Filter by urgency: critical/watch/ok/anomaly.
    Returns days remaining, supplier, and margin for each item.
    Use this to answer: 'kya order karna hai?', 'kya khatam ho raha hai?'
    """
    return get_stock_status(urgency=urgency, limit=limit)


@mcp.tool()
def item_velocity(item_code: str, include_history: bool = False) -> dict:
    """
    Get sales velocity and trend for a specific item.
    Returns avg daily sales (7d/30d/90d), trend, confidence, and suggested reorder qty.
    Use item_code from search_item() if you don't know the exact code.
    """
    return get_item_velocity(item_code=item_code, include_history=include_history)


@mcp.tool()
def dead_stock(days: int = 90, limit: int = 20) -> dict:
    """
    Find items with no sales in N days. Sorted by locked capital value.
    Use this to identify capital that could be freed up or returned to vendors.
    """
    return get_dead_stock(days=days, limit=limit)


@mcp.tool()
def find_item(query: str) -> dict:
    """
    Search for an item by name (partial ok) or exact code.
    Use this FIRST when the owner mentions a product by name
    to get the item_code needed for other tools.
    Example: find_item('amikacin') → returns code 'A00010'
    """
    return search_item(query_str=query)


@mcp.tool()
def supplier_info(item_code: str = None, supplier_name: str = None) -> dict:
    """
    Get supplier details and rate trends for an item or vendor.
    Provide item_code to find who supplies it and at what rate.
    Provide supplier_name to see their overall rate trend.
    Use before drafting an order to confirm current rate.
    """
    return get_supplier_info(item_code=item_code, supplier_name=supplier_name)


@mcp.tool()
def margin_alerts(threshold_pct: float = 8.0) -> dict:
    """
    Find items with dangerously low or eroding profit margins.
    Critical = below 3%. Watch = below threshold (default 8%).
    Use this for weekly profitability review or when owner asks about margins.
    """
    return get_margin_alerts(threshold_pct=threshold_pct)


@mcp.tool()
def anomalies(severity: str = None, anomaly_type: str = None) -> dict:
    """
    Get all flagged issues for today. The best starting point for any brief.
    severity: critical/high/medium/low
    anomaly_type: negative_stock/critical_stock/margin_erosion/dead_stock
    Call with no args first to see everything, then filter for details.
    """
    return get_anomalies(severity=severity, anomaly_type=anomaly_type)


@mcp.tool()
def vendor_message_draft(
    item_code: str,
    quantity: int,
    supplier_name: str,
    last_rate: float,
    notes: str = None
) -> dict:
    """
    Draft a WhatsApp order message to a supplier in Hinglish.
    ALWAYS show the draft to the owner for confirmation before sending.
    Never suggest sending automatically — owner must approve every order.
    Get item_code from find_item(), supplier from supplier_info().
    """
    return draft_vendor_message(
        item_code=item_code,
        quantity=quantity,
        supplier_name=supplier_name,
        last_rate=last_rate,
        notes=notes
    )


# ────────────────────────────────────────────
# RESOURCES
# ────────────────────────────────────────────

@mcp.resource("vaidya://brief/today")
def daily_brief() -> str:
    """
    Today's pre-generated morning business brief for Magadh Wellness.
    Generated at 8 AM by the scheduler. Read this first in the morning.
    """
    return get_daily_brief_content()


@mcp.resource("vaidya://pipeline/status")
def pipeline_status() -> str:
    """
    Data pipeline health: last sync times, data freshness, snapshot count.
    Check this if data seems stale or if velocity confidence is low.
    """
    return get_pipeline_status_content()


@mcp.resource("vaidya://schema/summary")
def schema_summary() -> str:
    """
    Summary of the Vaidya-AI database schema.
    Use this context when reasoning about what data is available.
    """
    return get_schema_summary_content()


# ────────────────────────────────────────────
# ENTRY POINT
# ────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Starting Vaidya-AI MCP Server...")
    logger.info("Tools: stock_status, item_velocity, dead_stock, find_item, "
                "supplier_info, margin_alerts, anomalies, vendor_message_draft")
    logger.info("Resources: vaidya://brief/today, vaidya://pipeline/status, "
                "vaidya://schema/summary")

    # Verify Supabase on startup
    try:
        from core.database import get_supabase
        get_supabase()
        logger.info("Supabase connection OK")
    except Exception as e:
        logger.error(f"Supabase connection FAILED: {e}")
        logger.error("Set SUPABASE_URL and SUPABASE_KEY in .env")
        sys.exit(1)

    mcp.run()
