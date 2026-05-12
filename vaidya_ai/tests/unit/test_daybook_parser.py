"""
tests/unit/test_daybook_parser.py

Unit tests for DaybookParser — the Item Day Book CSV parser.

Tests cover every edge case observed in real Marg exports:
  - Multiple party-name/city header formats
  - SALE vs PURC transaction types
  - Scheme quantities (50+10)
  - Placeholder suppliers (-BLANK-)
  - Zero-MRP capital equipment
  - Concatenated city in party name (LIMITEDGAYA)
  - Run-on bill numbers (NASA12345NATIONAL DRUG)
  - Page-break artifacts (Continued..2, MAGADH WELLNESS headers)
  - Missing batch/expiry on some rows

Run with:
  python -m pytest tests/unit/test_daybook_parser.py -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import unittest
from datetime import date

import pandas as pd

from tests.fixtures.generate_fixtures import make_daybook_fixture
from pipeline.parsers.daybook_parser import DaybookParser


FIXTURE_PATH = make_daybook_fixture()


class TestDaybookParserHappyPath(unittest.TestCase):

    def setUp(self):
        self.parser = DaybookParser()
        self.result = self.parser.parse(FIXTURE_PATH, date(2026, 5, 10))

    def test_parse_succeeds(self):
        self.assertTrue(self.result.success,
                        f"Parse failed: {self.result.errors}")

    def test_returns_dataframe(self):
        self.assertIsNotNone(self.result.data)
        self.assertIsInstance(self.result.data, pd.DataFrame)

    def test_correct_row_count(self):
        # Fixture has 13 item lines: 11 SALE/PURC + 2 ST.L (stock loan)
        self.assertEqual(self.result.row_count, 13,
                         f"Expected 13 rows, got {self.result.row_count}")

    def test_required_columns_present(self):
        required = ["date", "bill_no", "party_name", "transaction_type",
                    "item_code", "item_name", "qty", "rate", "amount"]
        for col in required:
            self.assertIn(col, self.result.data.columns,
                          f"Missing column: {col}")

    def test_metadata_attached(self):
        self.assertIn("_report_id", self.result.data.columns)
        self.assertEqual(self.result.data["_report_id"].iloc[0],
                         "item_daybook")


class TestDaybookParserBillHeaders(unittest.TestCase):

    def setUp(self):
        self.parser = DaybookParser()
        self.result = self.parser.parse(FIXTURE_PATH, date(2026, 5, 10))
        self.df = self.result.data

    def test_standard_bill_format(self):
        """10-04-2026 A000073  S S PHARMA  | GAYA"""
        rows = self.df[self.df["bill_no"] == "A000073"]
        self.assertFalse(rows.empty)
        self.assertEqual(rows["party_name"].iloc[0], "S S PHARMA")
        self.assertEqual(rows["city"].iloc[0], "GAYA")

    def test_concatenated_city(self):
        """A000078 has ARSH MEDI TECH PRIVATE LIMITEDGAYA (no space)"""
        rows = self.df[self.df["bill_no"] == "A000078"]
        self.assertFalse(rows.empty)
        self.assertEqual(rows["party_name"].iloc[0],
                         "ARSH MEDI TECH PRIVATE LIMITED")
        self.assertEqual(rows["city"].iloc[0], "GAYA")

    def test_run_on_vendor_bill_number(self):
        """NASA26270007NATIONAL DRUG + AGENCIES PATNA"""
        rows = self.df[self.df["bill_no"] == "NASA26270007"]
        self.assertFalse(rows.empty)
        self.assertEqual(rows["party_name"].iloc[0],
                         "NATIONAL DRUG AGENCIES")
        self.assertEqual(rows["city"].iloc[0], "PATNA")

    def test_date_iso_format(self):
        """All dates should be in YYYY-MM-DD format."""
        for d in self.df["date"]:
            self.assertRegex(d, r"^\d{4}-\d{2}-\d{2}$",
                             f"Date not in ISO format: {d}")


class TestDaybookParserTransactions(unittest.TestCase):

    def setUp(self):
        self.parser = DaybookParser()
        self.result = self.parser.parse(FIXTURE_PATH, date(2026, 5, 10))
        self.df = self.result.data

    def test_sale_and_purc_both_present(self):
        types = set(self.df["transaction_type"].unique())
        self.assertIn("SALE", types)
        self.assertIn("PURC", types)

    def test_stock_loan_recognised(self):
        """ST.L (Stock Loan to fellow stores at wholesale rate) must parse."""
        types = set(self.df["transaction_type"].unique())
        self.assertIn("ST.L", types,
                      "ST.L transaction type was dropped — these are real outward "
                      "stock movements and must be captured")

    def test_stock_loan_bill_format(self):
        """L-prefix bills (L000030) should parse despite having no city in col2."""
        loan_rows = self.df[self.df["bill_no"].astype(str).str.startswith("L")]
        self.assertFalse(loan_rows.empty, "Stock loan bills should be parsed")
        self.assertIn("DR MUKTA MANI",
                      loan_rows["party_name"].unique())

    def test_scheme_quantity_parsed(self):
        """50+10 SALE should yield qty=50 (10 is implicit free)."""
        row = self.df[self.df["item_code"] == "A13EAE97"]
        self.assertFalse(row.empty)
        self.assertEqual(row["qty"].iloc[0], 50)

    def test_zero_mrp_equipment(self):
        """Capital equipment has MRP=0 — should not crash."""
        row = self.df[self.df["item_code"] == "A00177"]
        self.assertFalse(row.empty)
        self.assertEqual(row["mrp"].iloc[0], 0.0)
        self.assertEqual(row["amount"].iloc[0], 152250.0)

    def test_blank_supplier_normalised_to_none(self):
        """-BLANK- in company column should become null (None or NaN)."""
        row = self.df[self.df["item_code"] == "00096"]
        self.assertFalse(row.empty)
        value = row["company"].iloc[0]
        # pandas may convert None to NaN — both represent "missing"
        self.assertTrue(value is None or pd.isna(value),
                        f"Expected None/NaN for -BLANK- supplier, got: {value!r}")

    def test_valid_supplier_preserved(self):
        row = self.df[self.df["item_code"] == "1242"]
        self.assertFalse(row.empty)
        self.assertEqual(row["company"].iloc[0], "ROMSONS")

    def test_amount_calculation_sanity(self):
        """qty * rate should approximately equal amount for most items."""
        # 5 × 151.42 = 757.10
        romsons = self.df[self.df["item_code"] == "1242"]
        self.assertAlmostEqual(romsons["amount"].iloc[0], 757.10, places=2)


class TestDaybookParserBatchExpiry(unittest.TestCase):

    def setUp(self):
        self.parser = DaybookParser()
        self.result = self.parser.parse(FIXTURE_PATH, date(2026, 5, 10))
        self.df = self.result.data

    def test_batch_extracted(self):
        row = self.df[self.df["item_code"] == "1242"]
        self.assertEqual(row["batch_no"].iloc[0], "G25G010803")

    def test_expiry_extracted(self):
        row = self.df[self.df["item_code"] == "1242"]
        self.assertEqual(row["expiry"].iloc[0], "Jun 2030")

    def test_missing_batch_handled(self):
        """Pulse oximeter has no batch/expiry — should be None, not crash."""
        row = self.df[self.df["item_code"] == "A13EAE114"]
        self.assertFalse(row.empty)
        self.assertTrue(pd.isna(row["batch_no"].iloc[0])
                        or row["batch_no"].iloc[0] is None)


class TestDaybookParserSkipLogic(unittest.TestCase):

    def setUp(self):
        self.parser = DaybookParser()
        self.result = self.parser.parse(FIXTURE_PATH, date(2026, 5, 10))
        self.df = self.result.data

    def test_no_header_rows_in_output(self):
        """MAGADH WELLNESS, ITEM DAY BOOK rows should be filtered."""
        for col in ["party_name", "item_name"]:
            for value in self.df[col].dropna():
                self.assertNotIn("MAGADH WELLNESS", str(value).upper())
                self.assertNotIn("ITEM DAY BOOK", str(value).upper())

    def test_no_continued_rows(self):
        for col in ["party_name", "item_name"]:
            for value in self.df[col].dropna():
                self.assertNotIn("Continued", str(value))


class TestDaybookParserErrorHandling(unittest.TestCase):

    def test_missing_file(self):
        parser = DaybookParser()
        result = parser.parse("/nonexistent/path.csv", date.today())
        self.assertFalse(result.success)
        self.assertTrue(any("not found" in e.lower() for e in result.errors))

    def test_wrong_extension(self):
        parser = DaybookParser()
        result = parser.parse("/some/file.xlsx", date.today())
        self.assertFalse(result.success)
        self.assertTrue(any("csv" in e.lower() for e in result.errors))


if __name__ == "__main__":
    unittest.main(verbosity=2)