"""
core/database.py

Supabase REST client using raw httpx — no supabase Python package needed.
The Supabase REST API is just PostgREST under the hood, which is simple HTTP.
This avoids the heavy supabase dependency chain that breaks on Python 3.14.

All tools import get_supabase() from here and call methods on the returned client.
The client interface is intentionally similar to the supabase-py client so
minimal changes are needed in the tools.
"""

import os
import logging
from functools import lru_cache
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class SupabaseRESTClient:
    """
    Minimal Supabase REST client using httpx.
    Implements the subset of the supabase-py interface used by the MCP tools.
    """

    def __init__(self, url: str, key: str):
        self.url = url.rstrip("/")
        self.headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    def table(self, table_name: str) -> "QueryBuilder":
        return QueryBuilder(self, table_name)

    def test_connection(self) -> bool:
        try:
            r = httpx.get(
                f"{self.url}/rest/v1/stock_items",
                headers={**self.headers, "Range": "0-0"},
                timeout=10,
            )
            return r.status_code in (200, 206, 416)
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False


class QueryResult:
    def __init__(self, data: list):
        self.data = data


class QueryBuilder:
    """Chainable PostgREST query builder."""

    def __init__(self, client: SupabaseRESTClient, table: str):
        self._client = client
        self._table = table
        self._select = "*"
        self._filters: list = []
        self._order_col: Optional[str] = None
        self._order_desc: bool = False
        self._limit: Optional[int] = None
        self._range_start: int = 0

    def select(self, columns: str) -> "QueryBuilder":
        self._select = columns
        return self

    def eq(self, col: str, val) -> "QueryBuilder":
        self._filters.append((col, "eq", val))
        return self

    def neq(self, col: str, val) -> "QueryBuilder":
        self._filters.append((col, "neq", val))
        return self

    def in_(self, col: str, vals: list) -> "QueryBuilder":
        self._filters.append((col, "in", vals))
        return self

    def ilike(self, col: str, pattern: str) -> "QueryBuilder":
        self._filters.append((col, "ilike", pattern))
        return self

    def gte(self, col: str, val) -> "QueryBuilder":
        self._filters.append((col, "gte", val))
        return self

    def lte(self, col: str, val) -> "QueryBuilder":
        self._filters.append((col, "lte", val))
        return self

    def is_(self, col: str, val) -> "QueryBuilder":
        self._filters.append((col, "is", val))
        return self

    def order(self, col: str, desc: bool = False) -> "QueryBuilder":
        self._order_col = col
        self._order_desc = desc
        return self

    def limit(self, n: int) -> "QueryBuilder":
        self._limit = n
        return self

    def range(self, start: int, end: int) -> "QueryBuilder":
        self._range_start = start
        self._limit = end - start + 1
        return self

    def execute(self) -> QueryResult:
        params: dict = {"select": self._select}

        for col, op, val in self._filters:
            if op == "eq":
                params[col] = f"eq.{val}"
            elif op == "neq":
                params[col] = f"neq.{val}"
            elif op == "in":
                csv = ",".join(str(v) for v in val)
                params[col] = f"in.({csv})"
            elif op == "ilike":
                params[col] = f"ilike.{val}"
            elif op == "gte":
                params[col] = f"gte.{val}"
            elif op == "lte":
                params[col] = f"lte.{val}"
            elif op == "is":
                params[col] = "is.null" if val is None else f"is.{val}"

        if self._order_col:
            direction = "desc" if self._order_desc else "asc"
            params["order"] = f"{self._order_col}.{direction}"

        headers = dict(self._client.headers)
        if self._limit is not None:
            end = self._range_start + self._limit - 1
            headers["Range"] = f"{self._range_start}-{end}"
            headers["Range-Unit"] = "items"
            headers["Prefer"] = "count=none"

        try:
            response = httpx.get(
                f"{self._client.url}/rest/v1/{self._table}",
                params=params,
                headers=headers,
                timeout=15,
            )
            if response.status_code in (200, 206):
                return QueryResult(response.json())
            elif response.status_code == 416:
                return QueryResult([])
            else:
                logger.error(
                    f"Query failed on {self._table}: "
                    f"{response.status_code} {response.text[:200]}"
                )
                return QueryResult([])
        except Exception as e:
            logger.error(f"httpx error on {self._table}: {e}")
            return QueryResult([])


@lru_cache(maxsize=1)
def get_supabase() -> SupabaseRESTClient:
    """Returns a cached Supabase REST client. Fails loudly if unconfigured."""
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_KEY", "")

    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_KEY must be set in .env"
        )

    client = SupabaseRESTClient(url, key)
    logger.info("Supabase REST client initialised (httpx, no supabase-py)")
    return client


def query(table: str, filters: dict = None, limit: int = 100) -> list:
    """Generic SELECT helper. Returns list of dicts, empty list on error."""
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