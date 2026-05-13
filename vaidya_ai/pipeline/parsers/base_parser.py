"""
pipeline/parsers/base_parser.py

Abstract base class for all Marg Excel parsers.

Every parser MUST extend this. The base class handles:
  - File loading and validation
  - Column aliasing (Marg changes column names between versions)
  - Type coercion with error logging (not crashing)
  - Parse result wrapping with metadata
  - Schema mismatch detection

ADDING A NEW PARSER:
  1. Subclass BaseParser
  2. Set self.schema = get_schema('your_report_id')
  3. Implement _post_process(df) for any report-specific logic
  4. That's it — everything else is inherited
"""

import logging
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

import pandas as pd

from config.report_schemas import ReportSchema, ColumnSpec

logger = logging.getLogger(__name__)


def _utcnow():
    """Timezone-aware UTC now — replaces deprecated datetime.utcnow()."""
    return datetime.now(timezone.utc)


@dataclass
class ParseResult:
    """
    Wraps the output of every parse operation.
    Always check .success before using .data.
    """
    success: bool
    report_id: str
    file_path: str
    report_date: Optional[date]
    data: Optional[pd.DataFrame]
    row_count: int = 0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    schema_mismatches: List[str] = field(default_factory=list)
    file_hash: str = ""
    parsed_at: datetime = field(default_factory=_utcnow)

    def summary(self) -> str:
        status = "OK" if self.success else "FAILED"
        return (
            f"[{status}] {self.report_id} | {self.file_path} | "
            f"{self.row_count} rows | {len(self.errors)} errors | "
            f"{len(self.warnings)} warnings"
        )


class BaseParser(ABC):
    """
    Abstract base for all Marg report parsers.
    Subclasses implement _post_process() for report-specific transformations.
    """

    def __init__(self, schema: ReportSchema):
        self.schema = schema
        self.logger = logging.getLogger(self.__class__.__name__)

    def parse(self, file_path: str, report_date: Optional[date] = None) -> ParseResult:
        """
        Main entry point. Call this for every parse operation.
        Returns a ParseResult — never raises on recoverable errors.
        """
        path = Path(file_path)
        errors = []
        warnings = []
        schema_mismatches = []

        # ── File checks ──
        # Check extension first — catches obvious type errors even for missing files
        if path.suffix.lower() not in (".xlsx", ".xls", ".csv"):
            return ParseResult(
                success=False, report_id=self.schema.report_id,
                file_path=file_path, report_date=report_date,
                data=None, errors=[f"Unsupported file type: {path.suffix}"]
            )

        if not path.exists():
            return ParseResult(
                success=False, report_id=self.schema.report_id,
                file_path=file_path, report_date=report_date,
                data=None, errors=[f"File not found: {file_path}"]
            )

        file_hash = self._hash_file(path)

        # ── Load raw ──
        try:
            df_raw = self._load_raw(path)
        except Exception as e:
            return ParseResult(
                success=False, report_id=self.schema.report_id,
                file_path=file_path, report_date=report_date,
                data=None, errors=[f"Failed to load file: {e}"],
                file_hash=file_hash
            )

        # ── Resolve columns ──
        df, mismatches = self._resolve_columns(df_raw)
        schema_mismatches.extend(mismatches)

        # ── Check required columns ──
        missing_required = self._check_required_columns(df)
        if missing_required:
            return ParseResult(
                success=False, report_id=self.schema.report_id,
                file_path=file_path, report_date=report_date,
                data=None,
                errors=[f"Missing required columns: {missing_required}"],
                schema_mismatches=schema_mismatches,
                file_hash=file_hash
            )

        # ── Type coercion ──
        df, coerce_warnings = self._coerce_types(df)
        warnings.extend(coerce_warnings)

        # ── Report-specific post-processing ──
        try:
            df, post_errors, post_warnings = self._post_process(df, report_date)
            errors.extend(post_errors)
            warnings.extend(post_warnings)
        except Exception as e:
            errors.append(f"Post-processing failed: {e}")
            self.logger.exception("Post-processing error")

        # ── Attach metadata ──
        df["_report_id"] = self.schema.report_id
        df["_file_path"] = str(file_path)
        df["_file_hash"] = file_hash
        df["_parsed_at"] = _utcnow().isoformat()
        if report_date:
            df["_report_date"] = report_date.isoformat()

        # ── Drop empty rows ──
        df = df.dropna(how="all")

        return ParseResult(
            success=len(errors) == 0,
            report_id=self.schema.report_id,
            file_path=file_path,
            report_date=report_date,
            data=df,
            row_count=len(df),
            errors=errors,
            warnings=warnings,
            schema_mismatches=schema_mismatches,
            file_hash=file_hash,
        )

    def _load_raw(self, path: Path) -> pd.DataFrame:
        """Load Excel or CSV into a raw DataFrame. Picks engine by extension."""
        suffix = path.suffix.lower()
        if suffix == ".csv":
            return pd.read_csv(path, skiprows=self.schema.skip_rows, dtype=str)
        engine = "xlrd" if suffix == ".xls" else "openpyxl"
        return pd.read_excel(
            path,
            sheet_name=self.schema.sheet_name or 0,
            skiprows=self.schema.skip_rows,
            dtype=str,
            engine=engine,
        )

    def _resolve_columns(self, df: pd.DataFrame):
        """
        Map actual column names to canonical names using aliases.
        Returns (renamed_df, list_of_mismatch_warnings).
        """
        mismatches = []
        rename_map = {}
        actual_cols = {c.strip().lower(): c for c in df.columns}

        for spec in self.schema.columns:
            canonical = spec.name
            candidates = [canonical] + spec.aliases
            found = False

            for candidate in candidates:
                if candidate.strip().lower() in actual_cols:
                    original = actual_cols[candidate.strip().lower()]
                    if original != canonical:
                        rename_map[original] = canonical
                    found = True
                    break

            if not found and spec.required:
                mismatches.append(
                    f"Column '{canonical}' not found. Tried: {candidates}"
                )
            elif not found:
                mismatches.append(
                    f"Optional column '{canonical}' not found — will be null"
                )

        df = df.rename(columns=rename_map)
        return df, mismatches

    def _check_required_columns(self, df: pd.DataFrame) -> List[str]:
        """Return list of required columns that are missing."""
        required = {spec.name for spec in self.schema.columns if spec.required}
        present = set(df.columns)
        return list(required - present)

    def _coerce_types(self, df: pd.DataFrame):
        """
        Coerce columns to their defined types.
        Logs warnings on failures but doesn't crash.
        """
        warnings = []
        type_map = {spec.name: spec.dtype for spec in self.schema.columns}

        for col, dtype in type_map.items():
            if col not in df.columns:
                continue
            try:
                if dtype == "float":
                    # Handle '###' (Excel overflow), commas, spaces
                    df[col] = (
                        df[col].astype(str)
                        .str.replace(",", "", regex=False)
                        .str.replace("###", "", regex=False)
                        .str.strip()
                    )
                    df[col] = pd.to_numeric(df[col], errors="coerce")

                elif dtype == "int":
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                    df[col] = df[col].astype("Int64")  # Nullable int

                elif dtype == "date":
                    df[col] = pd.to_datetime(df[col], errors="coerce", dayfirst=True)

                elif dtype == "str":
                    df[col] = df[col].astype(str).str.strip()
                    df[col] = df[col].replace({"nan": None, "None": None, "": None})

            except Exception as e:
                warnings.append(f"Type coercion failed for '{col}' ({dtype}): {e}")

        return df, warnings

    def _hash_file(self, path: Path) -> str:
        """MD5 hash of file content — used to detect duplicate uploads."""
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    @abstractmethod
    def _post_process(
        self,
        df: pd.DataFrame,
        report_date: Optional[date]
    ):
        """
        Report-specific transformations AFTER base cleaning.
        Must return: (df, errors: List[str], warnings: List[str])
        """
        ...