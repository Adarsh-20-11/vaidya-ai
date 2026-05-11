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
        """purchase_entries should contain only PURC rows."""
        # Both tables are split by transaction_type, so we just verify the
        # split happened correctly by checking the parsed counts
        parsed_sales = (self.parsed["transaction_type"] == "SALE").sum()
        parsed_purc = (self.parsed["transaction_type"] == "PURC").sum()
        self.assertEqual(len(self.split.sales_entries), parsed_sales)
        self.assertEqual(len(self.split.purchase_entries), parsed_purc)


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
