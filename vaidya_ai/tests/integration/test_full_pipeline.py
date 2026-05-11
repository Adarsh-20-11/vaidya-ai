"""
tests/integration/test_full_pipeline.py

Integration tests: parse → transform → local save (dry run, no Supabase).
These tests verify the full pipeline chain works end-to-end.

Run with: python -m pytest tests/integration/ -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import unittest
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

from tests.fixtures.generate_fixtures import (
    make_stock_current_fixture,
    make_stock_sales_fixture,
    make_ledger_fixture,
    make_snapshots_fixture,
)
from pipeline.parsers.stock_parser import StockParser
from pipeline.parsers.sales_parser import SalesParser
from pipeline.parsers.ledger_parser import LedgerParser
from pipeline.transformers.stock_transformer import StockTransformer
from pipeline.loaders.local_loader import LocalLoader


class TestFullStockPipeline(unittest.TestCase):
    """Parse stock → transform → save locally."""

    def setUp(self):
        self.report_date = date(2026, 5, 10)
        self.stock_file = make_stock_current_fixture()
        self.tmp_dir = tempfile.mkdtemp()

    def test_parse_then_save(self):
        parser = StockParser()
        result = parser.parse(self.stock_file, self.report_date)
        self.assertTrue(result.success, f"Parse failed: {result.errors}")

        loader = LocalLoader(output_dir=self.tmp_dir)
        path = loader.save("stock_current", result.data, str(self.report_date))
        self.assertTrue(Path(path).exists(), f"Output file not created: {path}")

        # Verify saved CSV is readable and correct
        saved = pd.read_csv(path)
        self.assertEqual(len(saved), result.row_count)

    def test_parse_then_transform_then_save(self):
        """Full chain: parse stock → build snapshots → transform → save intelligence tables."""
        # Parse
        parser = StockParser()
        parse_result = parser.parse(self.stock_file, self.report_date)
        self.assertTrue(parse_result.success)

        # Build multi-day snapshot from fixture (simulates 45 days of history)
        snapshots = make_snapshots_fixture()

        # Transform
        transformer = StockTransformer()
        transform_result = transformer.transform(snapshots, as_of_date=self.report_date)
        self.assertTrue(transform_result.success, f"Transform failed: {transform_result.errors}")

        # Save all intelligence tables
        loader = LocalLoader(output_dir=self.tmp_dir)
        saved_paths = loader.save_all(transform_result.tables, str(self.report_date))

        # Verify all 4 intelligence tables were produced and saved
        expected_tables = ["item_velocity", "item_health", "anomalies_today", "supplier_intelligence"]
        for table in expected_tables:
            self.assertIn(table, saved_paths, f"Missing table: {table}")
            self.assertTrue(Path(saved_paths[table]).exists())


class TestFullSalesPipeline(unittest.TestCase):

    def setUp(self):
        self.report_date = date(2026, 5, 9)
        self.sales_file = make_stock_sales_fixture()
        self.tmp_dir = tempfile.mkdtemp()

    def test_sales_parse_and_save(self):
        parser = SalesParser()
        result = parser.parse(self.sales_file, self.report_date)

        # May have warnings but should succeed
        self.assertIsNotNone(result.data)

        loader = LocalLoader(output_dir=self.tmp_dir)
        if result.data is not None and not result.data.empty:
            path = loader.save("stock_sales", result.data, str(self.report_date))
            self.assertTrue(Path(path).exists())


class TestFullLedgerPipeline(unittest.TestCase):

    def setUp(self):
        self.report_date = date(2026, 5, 10)
        self.ledger_file = make_ledger_fixture()
        self.tmp_dir = tempfile.mkdtemp()

    def test_ledger_parse_and_save(self):
        parser = LedgerParser(credit_days=30)
        result = parser.parse(self.ledger_file, self.report_date)
        self.assertIsNotNone(result.data)

        loader = LocalLoader(output_dir=self.tmp_dir)
        if result.data is not None and not result.data.empty:
            path = loader.save("ledger", result.data, str(self.report_date))
            self.assertTrue(Path(path).exists())


class TestPipelineIdempotency(unittest.TestCase):
    """Running the pipeline twice with the same file should produce the same result."""

    def setUp(self):
        self.report_date = date(2026, 5, 10)
        self.stock_file = make_stock_current_fixture()

    def test_parse_twice_same_result(self):
        parser = StockParser()
        result1 = parser.parse(self.stock_file, self.report_date)
        result2 = parser.parse(self.stock_file, self.report_date)

        self.assertEqual(result1.success, result2.success)
        self.assertEqual(result1.row_count, result2.row_count)
        self.assertEqual(result1.file_hash, result2.file_hash)

    def test_transform_twice_same_result(self):
        snapshots = make_snapshots_fixture()
        transformer = StockTransformer()

        result1 = transformer.transform(snapshots, as_of_date=self.report_date)
        result2 = transformer.transform(snapshots, as_of_date=self.report_date)

        self.assertEqual(result1.success, result2.success)
        self.assertEqual(
            set(result1.tables.keys()),
            set(result2.tables.keys())
        )


class TestSchemaValidation(unittest.TestCase):
    """Tests that schema mismatches are caught and reported."""

    def test_mismatched_column_names_produce_warning(self):
        """If Marg changes a column name, the parser should warn not crash."""
        import pandas as pd
        import tempfile

        # Create a file with wrong column name
        df = pd.DataFrame({
            "code": ["001"],
            "WRONG_NAME_FOR_PRODUCT": ["Test Item"],  # Should be 'name' or alias
            "Current Stock": [10],
            "Cost Price": [5.0],
            "M.R.P.": [8.0],
            "Purchase Price": [5.0],
        })
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            df.to_excel(f.name, index=False)
            tmp_path = f.name

        parser = StockParser()
        result = parser.parse(tmp_path, date.today())

        # Should have schema mismatches but not necessarily fail completely
        self.assertTrue(
            len(result.schema_mismatches) > 0,
            "Expected schema mismatch warnings for unknown column names"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
