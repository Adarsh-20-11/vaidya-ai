"""
pipeline/loaders/local_loader.py

Saves pipeline output to local CSV files.
Used for:
  - Development (no Supabase credentials needed)
  - Dry runs (--dry-run flag)
  - Debugging (inspect what would be uploaded)
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from config.settings import pipeline_config

logger = logging.getLogger(__name__)


class LocalLoader:

    def __init__(self, output_dir: Optional[str] = None):
        self.output_dir = Path(output_dir or pipeline_config.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save(self, table: str, df: pd.DataFrame, report_date: Optional[str] = None) -> str:
        """
        Save DataFrame to CSV. Filename includes table name and timestamp.
        Returns the path written to.
        """
        if df is None or df.empty:
            logger.info(f"Skipping {table} — empty DataFrame")
            return ""

        date_str = report_date or datetime.utcnow().strftime("%Y%m%d")
        filename = f"{table}_{date_str}.csv"
        path = self.output_dir / filename

        df.to_csv(path, index=False)
        logger.info(f"Saved {len(df)} rows to {path}")
        return str(path)

    def save_all(self, tables: dict, report_date: Optional[str] = None) -> dict:
        """Save multiple tables. Returns dict of {table_name: file_path}."""
        return {
            name: self.save(name, df, report_date)
            for name, df in tables.items()
            if df is not None and not df.empty
        }
