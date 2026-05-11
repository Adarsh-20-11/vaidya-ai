"""
core/database.py

Supabase client singleton for the MCP server.
All tools import from here — never create their own clients.
"""

import os
import logging
from functools import lru_cache
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_supabase():
    """
    Returns a cached Supabase client.
    Fails loudly on startup if credentials are missing —
    better to crash at boot than silently return wrong data.
    """
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_KEY", "")

    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_KEY must be set in .env\n"
            "Copy .env.example to .env and fill in your Supabase credentials."
        )

    from supabase import create_client
    client = create_client(url, key)
    logger.info("Supabase client initialised")
    return client


def query(table: str, filters: dict = None, limit: int = 100) -> list:
    """
    Generic SELECT helper used by tools.
    Returns list of dicts. Empty list on error (never raises).
    """
    try:
        client = get_supabase()
        q = client.table(table).select("*")
        if filters:
            for col, val in filters.items():
                q = q.eq(col, val)
        result = q.limit(limit).execute()
        return result.data or []
    except Exception as e:
        logger.error(f"Query failed on {table}: {e}")
        return []


def query_view(view: str, filters: dict = None,
               order_by: str = None, desc: bool = False,
               limit: int = 100) -> list:
    """SELECT from a view with optional ordering."""
    try:
        client = get_supabase()
        q = client.table(view).select("*")
        if filters:
            for col, val in filters.items():
                if val is not None:
                    q = q.eq(col, val)
        if order_by:
            q = q.order(order_by, desc=desc)
        result = q.limit(limit).execute()
        return result.data or []
    except Exception as e:
        logger.error(f"View query failed on {view}: {e}")
        return []
