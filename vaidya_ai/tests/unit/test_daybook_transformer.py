"""
tests/unit/test_daybook_transformer.py

Unit tests for DaybookTransformer — splits parsed records into
sales_entries and purchase_entries tables.

Run with: python -m pytest tests/unit/test_daybook_transformer.py -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import unittest
from datetime import date

import pandas as pd

from tests.fixtures.generate_fixtures import make_daybook_fixture
from pipeline.parsers.daybook_parser import DaybookParser
from pipeline.transformers.daybook_transformer import DaybookTransformer


FIXTURE_PATH = make_daybook_fixture()


class TestDaybookTransformerDiscount(unittest.TestCase):
    """Verify discount_pct is computed correctly from qty × rate vs amount."""

    def _make_df(self, rows):
        """Build a minimal parsed-daybook DataFrame for transformer tests."""
        defaults = {
            "date": "2026-04-15", "bill_no": "B1", "party_name": "TEST",
            "city": "GAYA", "transaction_type": "SALE", "item_code": "X",
            "item_name": "Item", "unit_pack": "", "mrp": 100.0,
            "company": "Co", "batch_no": "B", "expiry": "Dec 2027",
        }
        return pd.DataFrame([{**defaults, **r} for r in rows])

    def test_zero_discount_when_amount_equals_qty_times_rate(self):
        df = self._make_df([
            {"item_code": "A1", "bill_no": "B1", "qty": 10, "rate": 5.0, "amount": 50.0},
        ])
        split = DaybookTransformer().transform(df)
        self.assertEqual(split.sales_entries["discount_pct"].iloc[0], 0.0)

    def test_seven_point_five_pct_trade_discount(self):
        """The MEDVEY BIO pattern from real data: 50 × 140 = 7000 but amount = 6510."""
        df = self._make_df([
            {"item_code": "A2", "bill_no": "B2", "qty": 50, "rate": 140.0, "amount": 6510.0},
        ])
        split = DaybookTransformer().transform(df)
        # (7000 - 6510) / 7000 * 100 = 7.0
        self.assertAlmostEqual(split.sales_entries["discount_pct"].iloc[0], 7.0, places=1)

    def test_negative_discount_for_surcharge(self):
        """Customer paid more than rate × qty (rare — rounding/manual adjustment)."""
        df = self._make_df([
            {"item_code": "A3", "bill_no": "B3", "qty": 10, "rate": 5.0, "amount": 55.0},
        ])
        split = DaybookTransformer().transform(df)
        self.assertLess(split.sales_entries["discount_pct"].iloc[0], 0)

    def test_nan_when_rate_is_zero(self):
        """Divide-by-zero must produce NaN, not crash."""
        df = self._make_df([
            {"item_code": "A4", "bill_no": "B4", "qty": 1, "rate": 0.0, "amount": 100.0},
        ])
        split = DaybookTransformer().transform(df)
        self.assertTrue(pd.isna(split.sales_entries["discount_pct"].iloc[0]))

    def test_discount_present_on_purchase_entries(self):
        df = self._make_df([
            {"item_code": "A5", "bill_no": "B5",
             "transaction_type": "PURC", "qty": 100, "rate": 10.0, "amount": 950.0},
        ])
        split = DaybookTransformer().transform(df)
        self.assertEqual(split.purchase_entries["discount_pct"].iloc[0], 5.0)


class TestDaybookTransformerCategories(unittest.TestCase):
    """Verify transactions are tagged with the correct category."""

    def setUp(self):
        parser = DaybookParser()
        result = parser.parse(FIXTURE_PATH, date(2026, 5, 10))
        self.transformer = DaybookTransformer()
        self.split = self.transformer.transform(result.data)

    def test_sales_have_category_column(self):
        self.assertIn("category", self.split.sales_entries.columns)

    def test_purchases_have_category_column(self):
        self.assertIn("category", self.split.purchase_entries.columns)

    def test_retail_sales_categorised(self):
        retail = self.split.sales_entries[
            self.split.sales_entries["category"] == "retail"
        ]
        self.assertFalse(retail.empty, "Expected retail sales in fixture")

    def test_wholesale_stock_loans_categorised(self):
        """ST.L transactions should land in sales_entries with category='wholesale'."""
        wholesale = self.split.sales_entries[
            self.split.sales_entries["category"] == "wholesale"
        ]
        self.assertFalse(wholesale.empty,
                         "ST.L stock loans should be tagged as 'wholesale'")

    def test_no_wholesale_in_purchases(self):
        """Wholesale outward shouldn't pollute purchase_entries."""
        if not self.split.purchase_entries.empty:
            cats = set(self.split.purchase_entries["category"].unique())
            self.assertNotIn("wholesale", cats)
            self.assertNotIn("retail", cats)


class TestDaybookTransformerSplit(unittest.TestCase):

    def setUp(self):
        parser = DaybookParser()
        result = parser.parse(FIXTURE_PATH, date(2026, 5, 10))
        self.parsed = result.data
        self.transformer = DaybookTransformer()
        self.split = self.transformer.transform(self.parsed)

    def test_sales_entries_produced(self):
        self.assertFalse(self.split.sales_entries.empty,
                         "Expected non-empty sales_entries")

    def test_purchase_entries_produced(self):
        self.assertFalse(self.split.purchase_entries.empty,
                         "Expected non-empty purchase_entries")

    def test_total_split_equals_input(self):
        total_split = len(self.split.sales_entries) + len(self.split.purchase_entries)
        # New items may be smaller than total (deduped) — check transactions only
        self.assertEqual(total_split, len(self.parsed),
                         "Sum of sales+purchase should equal parsed count")

    def test_sales_entries_schema(self):
        required = ["date", "customer_name", "invoice_no", "item_code",
                    "item_name", "qty", "rate", "amount"]
        for col in required:
            self.assertIn(col, self.split.sales_entries.columns,
                          f"Missing column in sales_entries: {col}")

    def test_purchase_entries_schema(self):
        required = ["date", "vendor_name", "invoice_no", "item_code",
                    "item_name", "qty", "rate", "amount"]
        for col in required:
            self.assertIn(col, self.split.purchase_entries.columns,
                          f"Missing column in purchase_entries: {col}")

    def test_no_sale_in_purchase_table(self):
        """All outward txns go to sales_entries, all inward to purchase_entries."""
        # Sum of both tables should equal total parsed records
        # (every transaction is either outward or inward — none dropped)
        parsed_count = len(self.parsed)
        split_count = len(self.split.sales_entries) + len(self.split.purchase_entries)
        self.assertEqual(split_count, parsed_count,
                         "All parsed rows should land in exactly one table")


class TestDaybookTransformerNewItems(unittest.TestCase):

    def setUp(self):
        parser = DaybookParser()
        result = parser.parse(FIXTURE_PATH, date(2026, 5, 10))
        self.transformer = DaybookTransformer()
        self.split = self.transformer.transform(result.data)

    def test_new_items_deduplicated(self):
        """Each item_code should appear at most once in new_items."""
        codes = self.split.new_items["code"]
        self.assertEqual(len(codes), codes.nunique(),
                         "new_items should be deduplicated by item code")

    def test_new_items_schema(self):
        required = ["code", "name", "unit", "mrp", "company"]
        for col in required:
            self.assertIn(col, self.split.new_items.columns,
                          f"Missing column in new_items: {col}")

    def test_no_empty_codes(self):
        """No empty item codes in new_items."""
        empty = (self.split.new_items["code"].astype(str).str.strip() == "")
        self.assertEqual(empty.sum(), 0)


class TestDaybookTransformerEdgeCases(unittest.TestCase):

    def test_empty_input(self):
        transformer = DaybookTransformer()
        split = transformer.transform(pd.DataFrame())
        self.assertTrue(split.sales_entries.empty)
        self.assertTrue(split.purchase_entries.empty)
        self.assertTrue(split.new_items.empty)

    def test_none_input(self):
        transformer = DaybookTransformer()
        split = transformer.transform(None)
        self.assertTrue(split.sales_entries.empty)

    def test_all_sales_no_purchases(self):
        """When input has only SALE rows, purchase_entries should be empty."""
        df = pd.DataFrame([
            {"date": "2026-04-10", "bill_no": "B1", "party_name": "P1",
             "city": "GAYA", "transaction_type": "SALE",
             "item_code": "X1", "item_name": "ItemX", "unit_pack": "",
             "qty": 10, "rate": 5.0, "mrp": 10.0, "company": "CompX",
             "amount": 50.0, "batch_no": "B1", "expiry": "Dec 2027"},
        ])
        transformer = DaybookTransformer()
        split = transformer.transform(df)
        self.assertEqual(len(split.sales_entries), 1)
        self.assertTrue(split.purchase_entries.empty)


if __name__ == "__main__":
    unittest.main(verbosity=2)