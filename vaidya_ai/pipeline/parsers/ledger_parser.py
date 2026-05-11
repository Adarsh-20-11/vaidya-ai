"""
pipeline/parsers/ledger_parser.py

Parser for Marg Silver → Accounts → Party Ledger (Excel export).

WHAT IT DOES:
  - Extracts party name from the report header (not a data column)
  - Parses transaction rows: date, voucher type, debit, credit, balance
  - Infers balance direction (Dr = party owes Magadh, Cr = Magadh owes party)
  - Flags overdue invoices based on date
  - Handles multi-party ledger exports (one party per section)

NOTE:
  Ledger exports are structurally different from stock exports.
  The party name appears as a header row, not a column.
  The parser must detect these header rows and assign party names accordingly.
  Until we see a real ledger export, _post_process does best-effort extraction.
"""

import re
from datetime import date, datetime, timedelta
from typing import Optional, List, Tuple

import pandas as pd

from config.report_schemas import get_schema
from pipeline.parsers.base_parser import BaseParser


# Typical credit terms in pharma distribution (days)
DEFAULT_CREDIT_DAYS = 30


class LedgerParser(BaseParser):

    def __init__(self, credit_days: int = DEFAULT_CREDIT_DAYS):
        super().__init__(schema=get_schema("ledger"))
        self.credit_days = credit_days

    def _post_process(
        self,
        df: pd.DataFrame,
        report_date: Optional[date]
    ) -> Tuple[pd.DataFrame, List[str], List[str]]:
        errors = []
        warnings = []

        # ── Extract party name from header rows if not a column ──
        if "party_name" not in df.columns or df["party_name"].isna().all():
            df, party_warnings = self._extract_party_from_headers(df)
            warnings.extend(party_warnings)

        # ── Compute net balance direction ──
        if "debit" in df.columns and "credit" in df.columns:
            df["net_amount"] = df["debit"].fillna(0) - df["credit"].fillna(0)

        # ── Flag overdue based on invoice date and credit terms ──
        if "date" in df.columns and report_date:
            cutoff = report_date - timedelta(days=self.credit_days)
            df["is_overdue"] = (
                (df["date"].dt.date < cutoff) &
                (df["net_amount"].fillna(0) > 0)  # debit balance = amount owed
            )
            overdue_count = df["is_overdue"].sum()
            if overdue_count > 0:
                warnings.append(
                    f"{overdue_count} transactions appear overdue "
                    f"(>{self.credit_days} days old with outstanding debit)"
                )

        # ── Compute days outstanding per transaction ──
        if "date" in df.columns and report_date:
            df["days_outstanding"] = (
                pd.Timestamp(report_date) - df["date"]
            ).dt.days.clip(lower=0)

        # ── Infer balance type (Dr/Cr) from balance column if present ──
        if "balance" in df.columns:
            # Marg sometimes appends 'Dr' or 'Cr' to balance values
            df["balance_type"] = df["balance"].astype(str).str.extract(
                r"(Dr|Cr|DR|CR)", expand=False
            ).str.upper()
            df["balance"] = pd.to_numeric(
                df["balance"].astype(str)
                .str.replace(r"[DrCR\s]", "", regex=True)
                .str.replace(",", ""),
                errors="coerce"
            )

        return df, errors, warnings

    def _extract_party_from_headers(
        self, df: pd.DataFrame
    ) -> Tuple[pd.DataFrame, List[str]]:
        """
        Marg ledger exports often have party name as a non-data row.
        This method attempts to detect and propagate party names.
        Returns updated df and list of warnings.
        """
        warnings = []
        current_party = None
        party_col = []

        # Heuristic: a "header" row has mostly NaN numeric columns
        # and a non-null string in the first column that looks like a name
        numeric_cols = df.select_dtypes(include="number").columns.tolist()

        for idx, row in df.iterrows():
            numeric_nulls = sum(1 for c in numeric_cols if pd.isna(row.get(c)))
            is_likely_header = (
                len(numeric_cols) > 0 and
                numeric_nulls == len(numeric_cols) and
                pd.notna(row.iloc[0]) and
                len(str(row.iloc[0]).strip()) > 3
            )

            if is_likely_header:
                current_party = str(row.iloc[0]).strip()

            party_col.append(current_party)

        df["party_name"] = party_col

        if current_party is None:
            warnings.append(
                "Could not extract party name from ledger headers. "
                "party_name will be null. Check the raw export format."
            )

        return df, warnings
