"""
pipeline/loaders/supabase_loader.py

Handles all writes to Supabase.
Uses upsert (not insert) so re-running is always safe.

Design principles:
  - Idempotent: running twice with the same data produces the same result
  - Batched: large DataFrames are chunked to avoid API limits
  - Logged: every upsert is recorded in the pipeline_runs table
  - Failsafe: errors are caught and returned, never raised
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Any

import pandas as pd

from config.settings import supabase_config

logger = logging.getLogger(__name__)

BATCH_SIZE = 500  # Supabase free tier is comfortable with 500 rows per request


@dataclass
class LoadResult:
    success: bool
    table: str
    rows_upserted: int = 0
    rows_skipped: int = 0
    errors: List[str] = field(default_factory=list)
    loaded_at: datetime = field(default_factory=datetime.utcnow)


class SupabaseLoader:

    def __init__(self):
        self._client = None
        self._connected = False

    def _get_client(self):
        """Lazy init — only connect when actually needed."""
        if self._client is None:
            if not supabase_config.is_configured():
                raise RuntimeError(
                    "Supabase not configured. Set SUPABASE_URL and SUPABASE_KEY in .env"
                )
            from supabase import create_client
            self._client = create_client(supabase_config.url, supabase_config.key)
        return self._client

    def upsert(
        self,
        table: str,
        df: pd.DataFrame,
        conflict_columns: List[str],
        drop_meta_columns: bool = True,
    ) -> LoadResult:
        """
        Upsert a DataFrame into a Supabase table.

        Args:
            table:            Supabase table name
            df:               Data to upsert
            conflict_columns: Columns that define uniqueness (for ON CONFLICT)
            drop_meta_columns: Drop internal _* pipeline columns before uploading
        """
        if df is None or df.empty:
            return LoadResult(success=True, table=table, rows_skipped=len(df) if df is not None else 0)

        try:
            client = self._get_client()
        except Exception as e:
            return LoadResult(success=False, table=table, errors=[str(e)])

        # Drop internal pipeline metadata columns
        if drop_meta_columns:
            meta_cols = [c for c in df.columns if c.startswith("_")]
            df = df.drop(columns=meta_cols, errors="ignore")

        # Replace NaN with None (Supabase expects null, not NaN)
        df = df.where(pd.notna(df), None)

        # Convert to records
        records = df.to_dict(orient="records")
        total = len(records)
        upserted = 0
        errors = []

        # Batch upload
        for i in range(0, total, BATCH_SIZE):
            batch = records[i : i + BATCH_SIZE]
            try:
                client.table(table).upsert(
                    batch,
                    on_conflict=",".join(conflict_columns)
                ).execute()
                upserted += len(batch)
                logger.info(f"Upserted batch {i//BATCH_SIZE + 1} to {table} ({len(batch)} rows)")
            except Exception as e:
                err_msg = f"Batch {i//BATCH_SIZE + 1} failed: {e}"
                errors.append(err_msg)
                logger.error(err_msg)

        return LoadResult(
            success=len(errors) == 0,
            table=table,
            rows_upserted=upserted,
            errors=errors
        )

    def log_pipeline_run(
        self,
        report_id: str,
        file_path: str,
        file_hash: str,
        row_count: int,
        success: bool,
        errors: List[str],
        warnings: List[str],
    ) -> None:
        """Write a pipeline run record to Supabase for auditability."""
        try:
            client = self._get_client()
            client.table("pipeline_runs").insert({
                "report_id": report_id,
                "file_path": file_path,
                "file_hash": file_hash,
                "row_count": row_count,
                "success": success,
                "errors": errors,
                "warnings": warnings,
                "ran_at": datetime.utcnow().isoformat()
            }).execute()
        except Exception as e:
            logger.warning(f"Failed to log pipeline run: {e}")
