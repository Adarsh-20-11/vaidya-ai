"""
tests/unit/test_stock_parser.py

Unit tests for StockParser.
Tests cover: happy path, edge cases, data quality anomalies.

Run with: python -m pytest tests/unit/test_stock_parser.py -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import unittest
from datetime import date
from pathlib import Path

import pandas as pd

# Generate fixtures before testing
from tests.fixtures.generate_fixtures import make_stock_current_fixture
from pipeline.parsers.stock_parser import StockParser


FIXTURE_PATH = make_stock_current_fixture()


class TestStockParserHappyPath(unittest.TestCase):

    def setUp(self):
        self.parser = StockParser()
        self.result = self.parser.parse(FIXTURE_PATH, date(2026, 5, 10))

    def test_parse_succeeds(self):
        self.assertTrue(self.result.success, f"Parse failed: {self.result.errors}")

    def test_returns_dataframe(self):
        self.assertIsNotNone(self.result.data)
        self.assertIsInstance(self.result.data, pd.DataFrame)

    def test_row_count_correct(self):
        # 8 rows in fixture, 1 "Total" row should be dropped
        self.assertEqual(self.result.row_count, 7,
                         f"Expected 7 rows, got {self.result.row_count}")

    def test_required_columns_present(self):
        required = ["code", "name", "stock", "cost", "mrp", "purchase_price"]
        for col in required:
            self.assertIn(col, self.result.data.columns, f"Missing column: {col}")

    def test_metadata_columns_attached(self):
        self.assertIn("_report_id", self.result.data.columns)
        self.assertIn("_report_date", self.result.data.columns)
        self.assertEqual(self.result.data["_report_id"].iloc[0], "stock_current")


class TestStockParserNegativeStock(unittest.TestCase):

    def setUp(self):
        self.parser = StockParser()
        self.result = self.parser.parse(FIXTURE_PATH, date(2026, 5, 10))

    def test_negative_stock_flagged(self):
        """Adult Diaper has stock = -3 in fixture."""
        df = self.result.data
        neg = df[df["stock"] < 0]
        self.assertTrue(len(neg) > 0, "Expected at least one negative stock item")
        self.assertTrue(neg["is_negative_stock"].all(),
                        "Negative stock items must have is_negative_stock=True")

    def test_positive_stock_not_flagged(self):
        df = self.result.data
        pos = df[df["stock"] > 0]
        self.assertFalse(pos["is_negative_stock"].any(),
                         "Positive stock items should not be flagged as negative")

    def test_negative_stock_warning_issued(self):
        self.assertTrue(
            any("negative stock" in w.lower() for w in self.result.warnings),
            f"Expected negative stock warning. Got: {self.result.warnings}"
        )


class TestStockParserSupplierNormalisation(unittest.TestCase):

    def setUp(self):
        self.parser = StockParser()
        self.result = self.parser.parse(FIXTURE_PATH, date(2026, 5, 10))

    def test_blank_supplier_becomes_none(self):
        """'-BLANK-' and 'ZZZZZ Z 100' should be normalised to None."""
        df = self.result.data
        self.assertFalse(
            df["company"].isin(["-BLANK-", "ZZZZZ Z 100"]).any(),
            "Placeholder supplier values should be normalised to None"
        )

    def test_valid_supplier_preserved(self):
        df = self.result.data
        omex_rows = df[df["code"] == "133"]
        self.assertFalse(omex_rows.empty)
        self.assertEqual(omex_rows["company"].iloc[0], "OMEX")

    def test_unknown_supplier_flag(self):
        df = self.result.data
        self.assertIn("supplier_unknown", df.columns)
        # Items with -BLANK- company should be flagged
        blank_rows = df[df["company"].isna()]
        self.assertTrue(blank_rows["supplier_unknown"].all())


class TestStockParserMarginCalculation(unittest.TestCase):

    def setUp(self):
        self.parser = StockParser()
        self.result = self.parser.parse(FIXTURE_PATH, date(2026, 5, 10))

    def test_margin_pct_computed(self):
        self.assertIn("margin_pct", self.result.data.columns)

    def test_margin_pct_correct_for_known_item(self):
        """AMIKACIN: MRP=22.50, purchase=19.39 → margin=(22.50-19.39)/22.50*100 = 13.82%"""
        df = self.result.data
        amikacin = df[df["code"] == "A00010"]
        self.assertFalse(amikacin.empty)
        expected = round((22.50 - 19.39) / 22.50 * 100, 2)
        actual = amikacin["margin_pct"].iloc[0]
        self.assertAlmostEqual(actual, expected, places=1)

    def test_zero_mrp_excluded_from_margin(self):
        """Adult Diaper has Sales Price = 0.0 in fixture."""
        df = self.result.data
        zero_sales = df[df["mrp"] == 0]
        if not zero_sales.empty:
            self.assertTrue(zero_sales["margin_pct"].isna().all(),
                            "Zero-MRP items must have null margin_pct")


class TestStockParserFileErrors(unittest.TestCase):

    def test_missing_file_returns_failure(self):
        parser = StockParser()
        result = parser.parse("/nonexistent/path/file.xlsx", date.today())
        self.assertFalse(result.success)
        self.assertTrue(any("not found" in e.lower() for e in result.errors))

    def test_unsupported_extension_returns_failure(self):
        parser = StockParser()
        result = parser.parse("/some/file.pdf", date.today())
        self.assertFalse(result.success)
        self.assertTrue(any("unsupported" in e.lower() for e in result.errors))


class TestStockParserHeaderFiltering(unittest.TestCase):

    def setUp(self):
        self.parser = StockParser()
        self.result = self.parser.parse(FIXTURE_PATH, date(2026, 5, 10))

    def test_total_row_removed(self):
        """'Total' row in fixture should be filtered out."""
        df = self.result.data
        total_rows = df[df["code"].str.lower().str.contains("total", na=False)]
        self.assertEqual(len(total_rows), 0,
                         "Total/footer rows should be removed during parsing")

    def test_warning_issued_for_dropped_rows(self):
        self.assertTrue(
            any("dropped" in w.lower() or "header" in w.lower()
                for w in self.result.warnings),
            f"Expected header/footer drop warning. Got: {self.result.warnings}"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
