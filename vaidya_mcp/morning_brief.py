"""
morning_brief.py

Autonomous morning brief generator.
Runs at 8 AM via cron/scheduler.
Calls Claude with MCP tools connected, generates brief, saves to Supabase.

For Phase B (local dev): run manually to test
  python morning_brief.py

For Phase F (production): deploy as cron job on Railway
  Schedule: 0 8 * * *  (8 AM daily)

The brief is saved to daily_briefs table and served via
the vaidya://brief/today MCP resource.
The web UI polls for it and shows a notification when ready.
"""

import os
import sys
import logging
from datetime import date, datetime
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("morning_brief")


def generate_brief() -> str:
    """
    Runs Claude with MCP tools to generate the morning brief.
    Uses the Anthropic SDK with tool_choice to force tool usage.
    """
    import anthropic

    from tools.stock_tools import get_stock_status, get_dead_stock
    from tools.supplier_tools import get_anomalies, get_margin_alerts
    from prompts.system_prompt import SYSTEM_PROMPT, MORNING_BRIEF_PROMPT

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    # Pre-fetch key data for the brief
    # (avoids needing full MCP loop for scheduled job)
    logger.info("Fetching data for morning brief...")

    anomaly_data = get_anomalies()
    stock_critical = get_stock_status(urgency="critical", limit=10)
    stock_watch = get_stock_status(urgency="watch", limit=10)
    margin_data = get_margin_alerts()
    dead_data = get_dead_stock(limit=5)

    # Build context for Claude
    context = f"""
DATE: {date.today().strftime('%d %B %Y')}

ANOMALIES TODAY:
{_format_json(anomaly_data)}

CRITICAL STOCK ITEMS:
{_format_json(stock_critical)}

WATCH ITEMS:
{_format_json(stock_watch)}

MARGIN ALERTS:
{_format_json(margin_data)}

TOP DEAD STOCK (by value):
{_format_json(dead_data)}
""".strip()

    logger.info("Calling Claude to generate brief...")

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"{MORNING_BRIEF_PROMPT}\n\nDATA:\n{context}"
            }
        ]
    )

    brief_text = response.content[0].text
    logger.info(f"Brief generated ({len(brief_text)} chars)")
    return brief_text


def save_brief(brief_text: str) -> bool:
    """Save generated brief to Supabase daily_briefs table."""
    try:
        from core.database import get_supabase
        client = get_supabase()

        client.table("daily_briefs").upsert({
            "brief_date": date.today().isoformat(),
            "content": brief_text,
            "generated_at": datetime.utcnow().isoformat(),
        }, on_conflict="brief_date").execute()

        logger.info("Brief saved to Supabase daily_briefs")
        return True

    except Exception as e:
        logger.error(f"Failed to save brief: {e}")
        return False


def _format_json(data: dict) -> str:
    """Format dict for inclusion in prompt — compact but readable."""
    import json
    return json.dumps(data, indent=2, default=str)[:2000]  # Cap at 2000 chars


def main():
    logger.info("=== Morning Brief Generator ===")
    logger.info(f"Date: {date.today().isoformat()}")

    try:
        brief = generate_brief()

        print("\n" + "="*60)
        print("GENERATED BRIEF:")
        print("="*60)
        print(brief)
        print("="*60 + "\n")

        saved = save_brief(brief)
        if saved:
            logger.info("✅ Morning brief complete and saved")
        else:
            logger.warning("⚠️ Brief generated but not saved to Supabase")
            # Still exit 0 — brief was generated even if save failed
            print(brief, file=sys.stdout)

    except Exception as e:
        logger.error(f"Morning brief failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
