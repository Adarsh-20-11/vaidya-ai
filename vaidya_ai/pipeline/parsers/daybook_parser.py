"""
pipeline/parsers/daybook_parser.py

Parser for Marg Silver's "Item Day Book" report.

This is the most valuable single export from Marg — one file contains
every SALE and PURC line item across all bills in a date range.

Unlike other parsers in this pipeline, the daybook is NOT a flat tabular
report. It uses a paired-row format:

    Row N:   <date> <bill_no> <party_name>, <city>, , ,
    Row N+1: <item_code> <item_name>, <qty> SALE/PURC, <rate>, <mrp company>, <amount batch expiry>
    Row N+2: <item_code> <item_name>, <qty> SALE/PURC, ...   (more items on same bill)
    Row M:   <date> <bill_no> <party_name>, <city>, , ,     (next bill header)

So this parser overrides BaseParser.parse() entirely. It still produces a
ParseResult and follows the same contract (logs errors, never raises on
recoverable issues, attaches metadata) — but the internals are a state
machine, not column resolution.

Why override rather than fit into the base pattern? The base parser's
strength is column aliasing for flat tables. Forcing the daybook into that
shape would mean reading every column as string, then re-parsing
everything. Cleaner to acknowledge this is a special case.

INPUT FORMAT (5 raw CSV columns):
  Col 1: ITEM DESCRIPTION       → date+bill+party OR code+name
  Col 2: QUANTITY TYPE          → city OR "<qty> SALE/PURC"
  Col 3: RATE                   → empty OR numeric rate
  Col 4: M.R.P. COMPANY        → empty OR "<mrp> <company>" jammed
  Col 5: AMOUNT BATCH & DETAIL  → empty OR "<amount> <batch>  <expiry>"

OUTPUT: pandas.DataFrame with 15 unpacked columns (see ITEM_DAYBOOK_SCHEMA)
"""

import csv
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional, List, Tuple

import pandas as pd

from config.report_schemas import get_schema
from config.settings import business_rules
from pipeline.parsers.base_parser import BaseParser, ParseResult

logger = logging.getLogger(__name__)


# ── Regex patterns ──────────────────────────────────────────────────────────

# Bill number pattern: alphabetic prefix + digits, possibly run-on to party name
# Examples: A000073, NASA26270007, T000253
_BILL_NO_PATTERN = re.compile(r"^([A-Z]+\d+)")

# Item quantity + transaction type
# "75 SALE", "250 PURC", "50+10 SALE" (where 50 is sold qty, 10 is free)
_QTY_TYPE_PATTERN = re.compile(
    r"^([\d+]+)\s+(SALE|PURC|RETURN|SALERET)$", re.IGNORECASE
)

# MRP + Company jammed in col 4: "91.00 SI SURGICA" / " 0.00 MERIL DIAG"
_MRP_COMPANY_PATTERN = re.compile(r"^\s*([\d.]+)\s+(.+?)\s*$")

# Amount + Batch + Expiry jammed in col 5: "1144.50 26B3001  Jan 2031"
_AMOUNT_BATCH_PATTERN = re.compile(
    r"^\s*([\d.]+)\s*([A-Za-z0-9\-/]+)?\s*"
    r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})?\s*$",
    re.IGNORECASE,
)

# Unit pack at end of item name e.g. "1*200M", "1X4", "20 V"
_UNIT_PACK_PATTERN = re.compile(
    r"\s+(\d+[*xX]\d+[A-Za-z]*|\d+[A-Za-z]+\d*)\s*$"
)

# Rows that should be skipped entirely (page breaks, headers, totals)
_SKIP_PATTERNS = [
    re.compile(r"^MAGADH WELLNESS",   re.IGNORECASE),
    re.compile(r"^ITEM DAY BOOK",     re.IGNORECASE),
    re.compile(r"^ITEM DESCRIPTION",  re.IGNORECASE),
    re.compile(r"^Continued\.\.",     re.IGNORECASE),
    re.compile(r"^\s*Page\s*No",      re.IGNORECASE),
    re.compile(r"^TOTAL",             re.IGNORECASE),
]


def _utcnow():
    return datetime.now(timezone.utc)


class DaybookParser(BaseParser):
    """
    Parser for Marg Item Day Book CSV exports.
    Overrides BaseParser.parse() because the input format is paired-row,
    not flat tabular.
    """

    def __init__(self):
        super().__init__(schema=get_schema("item_daybook"))
        # Compile pattern using configured cities at init time
        cities = sorted(business_rules.known_cities, key=len, reverse=True)
        # Sort by length DESC so "BODH GAYA" matches before "GAYA"
        self._cities = cities

    # ── Public entry point (overrides BaseParser.parse) ──

    def parse(
        self, file_path: str, report_date: Optional[date] = None
    ) -> ParseResult:
        """Override base parse() because daybook is paired-row format."""
        path = Path(file_path)
        errors: List[str] = []
        warnings: List[str] = []

        # ── File checks (same contract as base) ──
        if path.suffix.lower() not in (".csv",):
            return ParseResult(
                success=False, report_id=self.schema.report_id,
                file_path=file_path, report_date=report_date,
                data=None,
                errors=[f"Daybook parser requires CSV input, got: {path.suffix}"],
            )

        if not path.exists():
            return ParseResult(
                success=False, report_id=self.schema.report_id,
                file_path=file_path, report_date=report_date,
                data=None, errors=[f"File not found: {file_path}"],
            )

        file_hash = self._hash_file(path)

        # ── Load raw rows (try multiple encodings) ──
        raw_rows = self._load_raw_csv(path)
        if raw_rows is None:
            return ParseResult(
                success=False, report_id=self.schema.report_id,
                file_path=file_path, report_date=report_date,
                data=None, errors=["Could not decode file with any known encoding"],
                file_hash=file_hash,
            )

        # ── Run state-machine parser ──
        records, parse_warnings, parse_errors = self._extract_records(raw_rows)
        warnings.extend(parse_warnings)
        errors.extend(parse_errors)

        if not records:
            return ParseResult(
                success=False, report_id=self.schema.report_id,
                file_path=file_path, report_date=report_date,
                data=None,
                errors=errors + ["No item records found — check file format"],
                warnings=warnings, file_hash=file_hash,
            )

        # ── Build DataFrame and attach metadata ──
        df = pd.DataFrame(records)
        df["_report_id"]  = self.schema.report_id
        df["_file_path"]  = str(file_path)
        df["_file_hash"]  = file_hash
        df["_parsed_at"]  = _utcnow().isoformat()
        if report_date:
            df["_report_date"] = report_date.isoformat()

        # ── Post-process for quality checks ──
        warnings.extend(self._quality_checks(df))

        return ParseResult(
            success=len(errors) == 0,
            report_id=self.schema.report_id,
            file_path=file_path,
            report_date=report_date,
            data=df,
            row_count=len(df),
            errors=errors,
            warnings=warnings,
            file_hash=file_hash,
        )

    # _post_process is required by BaseParser ABC but unused here
    def _post_process(self, df, report_date):
        return df, [], []

    # ── Internals ──

    def _load_raw_csv(self, path: Path) -> Optional[list]:
        """Try multiple encodings, return list of rows or None."""
        for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
            try:
                with open(path, "r", encoding=encoding, errors="replace") as f:
                    reader = csv.reader(f)
                    return list(reader)
            except Exception as e:
                self.logger.debug(f"Failed with {encoding}: {e}")
                continue
        return None

    def _extract_records(
        self, raw_rows: list
    ) -> Tuple[List[dict], List[str], List[str]]:
        """
        State machine:
          - skip junk rows
          - on bill-header row → update current_bill state
          - on item row → emit record using current_bill + item details

        Returns (records, warnings, errors).
        """
        records: List[dict] = []
        warnings: List[str] = []
        errors: List[str] = []
        current_bill = {"date": None, "bill_no": None,
                        "party_name": None, "city": None}

        unrecognised_count = 0

        for line_num, row in enumerate(raw_rows, start=1):
            # Pad to 5 columns for safety
            while len(row) < 5:
                row.append("")

            if self._should_skip(row):
                continue

            if self._is_bill_header(row):
                current_bill = self._parse_bill_header(row)
                continue

            # Must have a current bill to attach items to
            if current_bill["date"] is None:
                continue

            item = self._parse_item_row(row, current_bill)
            if item:
                records.append(item)
            else:
                if row[0].strip():  # only log non-empty mystery rows
                    unrecognised_count += 1
                    if unrecognised_count <= 5:  # cap log spam
                        self.logger.warning(
                            f"Line {line_num}: unrecognised row: {row[0][:60]}"
                        )

        if unrecognised_count > 0:
            warnings.append(
                f"{unrecognised_count} rows could not be classified as "
                f"bill-header or item-line and were skipped"
            )

        return records, warnings, errors

    def _should_skip(self, row: list) -> bool:
        if not any(c.strip() for c in row):
            return True
        col1 = row[0].strip()
        return any(p.search(col1) for p in _SKIP_PATTERNS)

    def _is_bill_header(self, row: list) -> bool:
        """
        Bill header: col1 starts with DD-MM-YYYY, cols 3+4 empty.
        Exception: when party name overflow into col2 makes city
        detectable in col2.
        """
        col1 = row[0].strip()
        if not re.match(r"^\d{2}-\d{2}-\d{4}", col1):
            return False
        col3 = row[2].strip() if len(row) > 2 else ""
        col4 = row[3].strip() if len(row) > 3 else ""
        # Pure header: cols 3 & 4 empty
        return not col3 and not col4

    def _parse_bill_header(self, row: list) -> dict:
        """
        Extract: date, bill_no, party_name, city.

        Handles three real-world formats:
          A. "10-04-2026 A000073     DR PIYUSH RANJAN", "         SASARAM"
          B. "11-04-2026 NASA26270007NATIONAL DRUG",    "AGENCIES        PATNA"
          C. "11-04-2026 A000078     ARSH MEDI TECH PRIVATE LIMITEDGAYA"
        """
        col1 = row[0].strip()
        col2 = row[1].strip() if len(row) > 1 else ""

        # Date (first 10 chars)
        date_str = col1[:10]
        try:
            date_formatted = datetime.strptime(
                date_str, "%d-%m-%Y"
            ).strftime("%Y-%m-%d")
        except ValueError:
            date_formatted = date_str  # keep raw if unparseable

        remainder = col1[10:].strip()

        # Extract bill number — handle run-on case "NASA26270007NATIONAL"
        bill_match = _BILL_NO_PATTERN.match(remainder)
        if bill_match:
            bill_no = bill_match.group(1)
            party_raw = remainder[len(bill_no):].strip()
        else:
            parts = remainder.split(None, 1)
            bill_no = parts[0] if parts else ""
            party_raw = parts[1].strip() if len(parts) > 1 else ""

        # Detect city — check col2 first, then end of party_name
        city = ""
        party_name = party_raw

        if col2:
            col2_upper = col2.upper()
            # Try exact match first
            if col2_upper in {c.upper() for c in self._cities}:
                city = col2_upper
            else:
                # Try city as suffix of col2 (with overflow before it)
                for known in self._cities:
                    if col2_upper.endswith(known):
                        city = known
                        overflow = col2[:len(col2) - len(known)].strip()
                        if overflow:
                            party_name = f"{party_raw} {overflow}".strip()
                        break
                else:
                    # No city found — col2 is pure party-name overflow
                    party_name = f"{party_raw} {col2}".strip()

        # City might be concatenated at end of party_name with no space
        # ("ARSH MEDI TECH PRIVATE LIMITEDGAYA")
        if not city:
            for known in self._cities:
                if party_name.upper().endswith(known):
                    city = known
                    party_name = party_name[:-len(known)].strip()
                    break

        return {
            "date": date_formatted,
            "bill_no": bill_no,
            "party_name": party_name.strip(),
            "city": city,
        }

    def _parse_item_row(self, row: list, current_bill: dict) -> Optional[dict]:
        """Parse one item line. Return None if row doesn't match."""
        col1 = row[0].strip()
        col2 = row[1].strip() if len(row) > 1 else ""
        col3 = row[2].strip() if len(row) > 2 else ""
        col4 = row[3].strip() if len(row) > 3 else ""
        col5 = row[4].strip() if len(row) > 4 else ""

        qty_match = _QTY_TYPE_PATTERN.match(col2)
        if not qty_match:
            return None

        qty_raw = qty_match.group(1)
        txn_type = qty_match.group(2).upper()

        # "50+10" → qty=50 (the free 10 is implicit; could be stored
        # separately later if needed for scheme tracking)
        qty = int(qty_raw.split("+")[0])

        # Item code + item name + optional unit pack
        col1_parts = col1.split(None, 1)
        item_code = col1_parts[0] if col1_parts else ""
        item_name_raw = col1_parts[1].strip() if len(col1_parts) > 1 else ""

        unit_pack = ""
        pack_match = _UNIT_PACK_PATTERN.search(item_name_raw)
        if pack_match:
            unit_pack = pack_match.group(1)
            item_name = item_name_raw[:pack_match.start()].strip()
        else:
            item_name = item_name_raw

        # Rate
        rate = self._safe_float(col3)

        # MRP + Company
        mrp, company = None, None
        if col4:
            m = _MRP_COMPANY_PATTERN.match(col4)
            if m:
                mrp = self._safe_float(m.group(1))
                company_raw = m.group(2).strip()
                # Normalise unknown supplier
                if company_raw.upper() in {
                    str(v).upper() for v in business_rules.unknown_supplier_values if v
                }:
                    company = None
                else:
                    company = company_raw

        # Amount + Batch + Expiry
        amount, batch_no, expiry = None, None, None
        if col5:
            m = _AMOUNT_BATCH_PATTERN.match(col5)
            if m:
                amount = self._safe_float(m.group(1))
                batch_no = m.group(2) or None
                expiry = m.group(3) or None
            else:
                # Fallback: try to extract just the leading number as amount
                num_match = re.match(r"^\s*([\d.]+)", col5)
                if num_match:
                    amount = self._safe_float(num_match.group(1))

        return {
            **current_bill,
            "transaction_type": txn_type,
            "item_code": item_code,
            "item_name": item_name,
            "qty": qty,
            "unit_pack": unit_pack,
            "rate": rate,
            "mrp": mrp,
            "company": company,
            "amount": amount,
            "batch_no": batch_no,
            "expiry": expiry,
        }

    def _safe_float(self, s: Optional[str]) -> Optional[float]:
        if not s:
            return None
        try:
            return float(str(s).strip())
        except (ValueError, TypeError):
            return None

    def _quality_checks(self, df: pd.DataFrame) -> List[str]:
        """Post-parse quality checks. Return list of warnings."""
        warnings = []

        # Check for items without supplier mapping
        if "company" in df.columns:
            blank = df["company"].isna().sum()
            if blank > 0:
                warnings.append(
                    f"{blank} item lines have no supplier "
                    f"(was -BLANK- or unmapped)"
                )

        # Zero-MRP items (capital equipment etc — informational)
        if "mrp" in df.columns:
            zero_mrp = (df["mrp"] == 0).sum()
            if zero_mrp > 0:
                warnings.append(
                    f"{zero_mrp} item lines have MRP=0 "
                    f"(typical for capital equipment; not an error)"
                )

        # Sales vs purchases split
        if "transaction_type" in df.columns:
            sales = (df["transaction_type"] == "SALE").sum()
            purc  = (df["transaction_type"] == "PURC").sum()
            warnings.append(
                f"Parsed {sales} SALE lines and {purc} PURC lines"
            )

        # Cross-check: amount should roughly equal qty × rate
        if all(c in df.columns for c in ["qty", "rate", "amount"]):
            mask = df["amount"].notna() & df["rate"].notna() & df["qty"].notna()
            check = df.loc[mask].copy()
            check["expected"] = check["qty"] * check["rate"]
            check["diff_pct"] = (
                (check["amount"] - check["expected"]).abs()
                / check["amount"].where(check["amount"] != 0)
            ) * 100
            bad = (check["diff_pct"] > 5).sum()
            if bad > 0:
                warnings.append(
                    f"{bad} item lines have amount != qty × rate "
                    f"(>5% deviation — possible discount/scheme not captured)"
                )

        return warnings
