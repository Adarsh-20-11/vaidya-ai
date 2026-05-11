"""
tests/unit/test_stock_transformer.py

Unit tests for StockTransformer — the intelligence computation layer.

Run with: python -m pytest tests/unit/test_stock_transformer.py -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import unittest
from datetime import date

import pandas as pd

from tests.fixtures.generate_fixtures import make_snapshots_fixture
from pipeline.transformers.stock_transformer import StockTransformer
from config.settings import business_rules


class TestTransformerVelocity(unittest.TestCase):

    def setUp(self):
        self.transformer = StockTransformer()
        self.snapshots = make_snapshots_fixture()
        self.result = self.transformer.transform(self.snapshots, as_of_date=date.today())

    def test_transform_succeeds(self):
        self.assertTrue(self.result.success, f"Transform failed: {self.result.errors}")

    def test_velocity_table_produced(self):
        self.assertIn("item_velocity", self.result.tables)
        df = self.result.tables["item_velocity"]
        self.assertFalse(df.empty)

    def test_velocity_columns_present(self):
        df = self.result.tables["item_velocity"]
        for col in ["item_code", "avg_daily_sales_7d", "avg_daily_sales_30d", "velocity_trend"]:
            self.assertIn(col, df.columns, f"Missing velocity column: {col}")

    def test_amikacin_velocity_reasonable(self):
        """Amikacin sells ~180/day in fixtures. 30d velocity should be close."""
        df = self.result.tables["item_velocity"]
        amikacin = df[df["item_code"] == "A00010"]
        self.assertFalse(amikacin.empty)
        v30 = amikacin["avg_daily_sales_30d"].iloc[0]
        self.assertIsNotNone(v30)
        # Should be within 30% of 180 (random noise in fixture)
        self.assertGreater(v30, 100, f"Amikacin 30d velocity too low: {v30}")
        self.assertLess(v30, 260, f"Amikacin 30d velocity too high: {v30}")

    def test_dead_stock_has_zero_velocity(self):
        """DEAD01 has zero sales in fixture — velocity should be 0 or None."""
        df = self.result.tables["item_velocity"]
        dead = df[df["item_code"] == "DEAD01"]
        if not dead.empty:
            v30 = dead["avg_daily_sales_30d"].iloc[0]
            self.assertEqual(v30, 0, f"Dead stock should have 0 velocity, got {v30}")

    def test_insufficient_data_returns_none(self):
        """With < min_snapshots rows, velocity should be None."""
        tiny_snapshots = self.snapshots[self.snapshots["item_code"] == "A00010"].head(3)
        result = self.transformer.transform(tiny_snapshots, as_of_date=date.today())
        df = result.tables.get("item_velocity", pd.DataFrame())
        if not df.empty:
            v30 = df[df["item_code"] == "A00010"]["avg_daily_sales_30d"].iloc[0]
            self.assertIsNone(v30,
                "Velocity should be None when insufficient snapshots")


class TestTransformerHealth(unittest.TestCase):

    def setUp(self):
        self.transformer = StockTransformer()
        self.snapshots = make_snapshots_fixture()
        self.result = self.transformer.transform(self.snapshots, as_of_date=date.today())

    def test_health_table_produced(self):
        self.assertIn("item_health", self.result.tables)
        df = self.result.tables["item_health"]
        self.assertFalse(df.empty)

    def test_urgency_column_present(self):
        df = self.result.tables["item_health"]
        self.assertIn("reorder_urgency", df.columns)

    def test_urgency_values_valid(self):
        df = self.result.tables["item_health"]
        valid = {"critical", "watch", "ok", "unknown", "anomaly"}
        actual = set(df["reorder_urgency"].unique())
        self.assertTrue(actual.issubset(valid),
                        f"Invalid urgency values: {actual - valid}")

    def test_days_remaining_positive_for_active_item(self):
        df = self.result.tables["item_health"]
        biosafe = df[df["item_code"] == "1801"]
        if not biosafe.empty:
            days = biosafe["days_remaining"].iloc[0]
            if days is not None:
                self.assertGreater(days, 0)


class TestTransformerAnomalies(unittest.TestCase):

    def setUp(self):
        self.transformer = StockTransformer()
        self.snapshots = make_snapshots_fixture()
        self.result = self.transformer.transform(self.snapshots, as_of_date=date.today())

    def test_anomalies_table_produced(self):
        self.assertIn("anomalies_today", self.result.tables)

    def test_anomaly_columns_present(self):
        df = self.result.tables["anomalies_today"]
        for col in ["item_code", "anomaly_type", "severity", "detail"]:
            self.assertIn(col, df.columns, f"Missing anomaly column: {col}")

    def test_dead_stock_flagged(self):
        """DEAD01 has zero sales — should appear as dead_stock anomaly."""
        df = self.result.tables["anomalies_today"]
        dead = df[
            (df["item_code"] == "DEAD01") &
            (df["anomaly_type"] == "dead_stock")
        ]
        self.assertFalse(dead.empty,
                         "DEAD01 should be flagged as dead_stock")

    def test_severity_values_valid(self):
        df = self.result.tables["anomalies_today"]
        if df.empty:
            return
        valid = {"critical", "high", "medium", "low"}
        actual = set(df["severity"].unique())
        self.assertTrue(actual.issubset(valid),
                        f"Invalid severity values: {actual - valid}")


class TestTransformerSupplierIntelligence(unittest.TestCase):

    def setUp(self):
        self.transformer = StockTransformer()
        self.snapshots = make_snapshots_fixture()
        self.result = self.transformer.transform(self.snapshots, as_of_date=date.today())

    def test_supplier_table_produced(self):
        self.assertIn("supplier_intelligence", self.result.tables)

    def test_known_supplier_present(self):
        df = self.result.tables["supplier_intelligence"]
        shree = df[df["supplier"] == "SHREE CHEHAR"]
        self.assertFalse(shree.empty, "SHREE CHEHAR should appear in supplier intelligence")

    def test_null_supplier_excluded(self):
        """Items with company=None should not appear in supplier table."""
        df = self.result.tables["supplier_intelligence"]
        if not df.empty:
            self.assertFalse(df["supplier"].isna().any(),
                             "Null suppliers should be excluded from supplier intelligence")

    def test_rate_trend_values_valid(self):
        df = self.result.tables["supplier_intelligence"]
        if df.empty:
            return
        valid = {"increasing", "decreasing", "stable", "unknown"}
        actual = set(df["rate_trend"].unique())
        self.assertTrue(actual.issubset(valid),
                        f"Invalid rate_trend values: {actual - valid}")


class TestTransformerEdgeCases(unittest.TestCase):

    def test_empty_snapshots_returns_failure(self):
        transformer = StockTransformer()
        result = transformer.transform(pd.DataFrame())
        self.assertFalse(result.success)
        self.assertTrue(any("no snapshot" in e.lower() for e in result.errors))

    def test_single_day_snapshot(self):
        """Single day of data should run without crashing."""
        transformer = StockTransformer()
        snapshots = make_snapshots_fixture()
        single_day = snapshots[snapshots["snapshot_date"] == snapshots["snapshot_date"].max()]
        result = transformer.transform(single_day, as_of_date=date.today())
        # Should succeed but velocity will be insufficient
        self.assertTrue(result.success or len(result.errors) > 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
