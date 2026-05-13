"""
pipeline/parsers/ledger_parser.py

Parser for Marg Silver's Outstanding Ledger report.
Point-in-time snapshot: one row per party, current balance only.

Input: XLS (5 columns)
  serial | party_name+city (space-padded) | group | debit | credit

Output columns:
  party_name, city, group, party_type, debit, credit, net_outstanding

party_type:
  'customer' — SUNDRY DEBTORS (they owe us)
  'vendor'   — SUNDRY CREDITORS (we owe them)
"""

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional, List

import pandas as pd

from config.report_schemas import get_schema
from config.settings import business_rules
from pipeline.parsers.base_parser import ParseResult

logger = logging.getLogger(__name__)

RELEVANT_GROUPS = {
    "SUNDRY DEBTORS",
    "SUNDRY CREDITORS (SUPPLIERS)",
    "SUNDRY CREDITORS (MANUFACTURERS)",
}

GROUP_TO_TYPE = {
    "SUNDRY DEBTORS":                   "customer",
    "SUNDRY CREDITORS (SUPPLIERS)":     "vendor",
    "SUNDRY CREDITORS (MANUFACTURERS)": "vendor",
}


def _utcnow():
    return datetime.now(timezone.utc)


def _split_party_city(raw: str, known_cities: list) -> tuple:
    """Split "PARTY NAME          CITY" into ("PARTY NAME", "CITY")."""
    raw = (raw or "").strip()
    for city in sorted(known_cities, key=len, reverse=True):
        if raw.upper().endswith(city.upper()):
            party = raw[:len(raw) - len(city)].strip()
            return party, city.upper()
    return raw, ""


class LedgerParser:
    """Parser for Marg Outstanding Ledger XLS export."""

    def __init__(self):
        self.schema = get_schema("outstanding_ledger")

    def parse(self, file_path: str, report_date: Optional[date] = None) -> ParseResult:
        path = Path(file_path)
        errors: List[str] = []
        warnings: List[str] = []

        if not path.exists():
            return ParseResult(
                success=False, report_id=self.schema.report_id,
                file_path=file_path, report_date=report_date,
                data=None, errors=[f"File not found: {file_path}"]
            )

        suffix = path.suffix.lower()
        if suffix not in (".xls", ".xlsx", ".csv"):
            return ParseResult(
                success=False, report_id=self.schema.report_id,
                file_path=file_path, report_date=report_date,
                data=None, errors=[f"Unsupported format: {suffix}"]
            )

        # ── Load ──
        try:
            if suffix == ".csv":
                raw = pd.read_csv(file_path, dtype=str, header=0)
            else:
                engine = "xlrd" if suffix == ".xls" else "openpyxl"
                raw = pd.read_excel(file_path, dtype=str, engine=engine, header=0)
        except Exception as e:
            return ParseResult(
                success=False, report_id=self.schema.report_id,
                file_path=file_path, report_date=report_date,
                data=None, errors=[f"Failed to load file: {e}"]
            )

        logger.info(f"Ledger raw: {len(raw)} rows, cols: {list(raw.columns)}")

        if len(raw.columns) < 5:
            return ParseResult(
                success=False, report_id=self.schema.report_id,
                file_path=file_path, report_date=report_date,
                data=None,
                errors=[f"Expected 5 columns, got {len(raw.columns)}"]
            )

        raw.columns = ["serial", "party_raw", "group", "debit_raw", "credit_raw"]
        raw["group"] = raw["group"].astype(str).str.strip().str.upper()

        # ── Filter to relevant groups only ──
        relevant = raw[raw["group"].isin(
            {g.upper() for g in RELEVANT_GROUPS}
        )].copy()

        if relevant.empty:
            warnings.append(
                "No SUNDRY DEBTORS or SUNDRY CREDITORS rows found — "
                "check column order or group names in the export."
            )

        # ── Split party name + city ──
        known_cities = [c.upper() for c in business_rules.known_cities]
        splits = relevant["party_raw"].apply(
            lambda r: pd.Series(
                _split_party_city(str(r), known_cities),
                index=["party_name", "city"]
            )
        )
        relevant = pd.concat([relevant, splits], axis=1)

        # ── Numeric columns ──
        def to_float(s):
            try:
                v = float(str(s).replace(",", "").strip())
                return v if v != 0 else None
            except (ValueError, TypeError):
                return None

        relevant["debit"]  = relevant["debit_raw"].apply(to_float)
        relevant["credit"] = relevant["credit_raw"].apply(to_float)
        relevant["net_outstanding"] = (
            relevant["debit"].fillna(0) - relevant["credit"].fillna(0)
        )

        # ── Party type ──
        relevant["party_type"] = relevant["group"].map(
            {k.upper(): v for k, v in GROUP_TO_TYPE.items()}
        ).fillna("unknown")

        # ── Output ──
        out = relevant[
            ["party_name", "city", "group", "party_type",
             "debit", "credit", "net_outstanding"]
        ].copy()
        out = out[out["party_name"].str.strip() != ""]
        out["_report_id"]   = self.schema.report_id
        out["_report_date"] = (report_date or date.today()).isoformat()
        out["_parsed_at"]   = _utcnow().isoformat()

        # ── Summary warnings ──
        customers = (out["party_type"] == "customer").sum()
        vendors   = (out["party_type"] == "vendor").sum()
        receivable = out.loc[out["party_type"] == "customer", "debit"].sum()
        payable    = out.loc[out["party_type"] == "vendor", "credit"].sum()
        warnings.append(
            f"Parsed {customers} customers (receivable ₹{receivable:,.0f}) "
            f"and {vendors} vendors (payable ₹{payable:,.0f})"
        )

        return ParseResult(
            success=True,
            report_id=self.schema.report_id,
            file_path=file_path,
            report_date=report_date,
            data=out,
            row_count=len(out),
            errors=errors,
            warnings=warnings,
        )