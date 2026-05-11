"""
tests/fixtures/generate_fixtures.py

Generates synthetic (anonymised) Excel fixture files for testing.
Run this once to create the fixture files that tests use.

All data is fictional — no real patient/business data in fixtures.
"""

import pandas as pd
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent


def make_stock_current_fixture() -> str:
    """Creates a stock_current.xlsx fixture matching real Marg export format."""
    data = {
        "code": ["137", "133", "A00010", "1801", "35", "A13EAE41", "00087", "Total"],
        "Product Name": [
            "3 BALL SPIROMETER", "3 WAY STOPCOCK KITKATH", "AMIKACIN 1*40 VIA",
            "BIOSAFE EXAMINATION GLOVES", "ADULT DIAPER SIZE L-XL 1X10",
            "BIO FFS PARACETAMO 1*100", "ADSON DISSECTING FORCEP",
            "Total Quantity"   # Header/footer row — should be filtered
        ],
        "Unit": ["PCS", "PCS", "PCS", "PCS", "BOX", "PCS", "PCS", None],
        "Current Stock": [34, 710, 2466, 35800, -3, 1500, 1, None],
        "Deal": [0, 0, 0, 0, 0, 0, 0, None],
        "Free": [0, 0, 0, 0, 0, 0, 0, None],
        "Cost Price": [56.0, 6.44, 19.39, 1.73, 215.0, 18.59, 201.60, None],
        "Value": [1904.0, 4572.4, 0, 61934.0, -645.0, 27885.0, 201.60, None],
        "M.R.P.": [70.25, 7.15, 22.50, 2.10, 250.0, 21.00, 225.0, None],
        "Purchase Price": [50.0, 5.75, 19.39, 1.73, 215.0, 18.59, 180.0, None],
        "Sales Price": [75.0, 7.15, 22.50, 2.10, 0.0, 21.00, 201.60, None],
        "Company": ["-BLANK-", "OMEX", "SHREE CHEHAR", "BIOSAFE", "ZZZZZ Z 100", "BIO", "-BLANK-", None],
        "Manufacturer": [None, None, None, None, None, None, "ADSON", None],
        "Rack No.": [None, None, None, None, None, None, None, None],
    }
    df = pd.DataFrame(data)
    path = FIXTURE_DIR / "stock_current.xlsx"
    df.to_excel(path, index=False)
    print(f"Created: {path}")
    return str(path)


def make_stock_sales_fixture() -> str:
    """Creates a stock_sales.xlsx fixture matching Marg Stock & Sales Analysis format."""
    data = {
        "CODE": ["137", "133", "A00010", "1801", "35", "Total"],
        "ITEM DESCRIPTION": [
            "3 BALL SPIROMETER", "3 WAY STOPCOCK",
            "AMIKACIN 1*40", "BIOSAFE GLOVES",
            "ADULT DIAPER", "Total Quantity"
        ],
        "OPENING STOCK": [39, 710, 2600, 36400, 0, None],
        "SALE": [5, 0, 134, 600, 3, None],
        "PURCHASES": [0, 0, 0, 0, 0, None],
        "REPL./ RETURN": [0, 0, 0, 0, 0, None],
        "REPL./ RETURN.1": [0, 0, 0, 0, 0, None],
        "CLOSING STOCK": [34, 710, 2466, 35800, -3, None],
        "RATE": [56.0, 6.44, 19.39, 1.73, 215.0, None],
    }
    df = pd.DataFrame(data)
    path = FIXTURE_DIR / "stock_sales.xlsx"
    df.to_excel(path, index=False)
    print(f"Created: {path}")
    return str(path)


def make_ledger_fixture() -> str:
    """Creates a ledger.xlsx fixture."""
    data = {
        "Date": ["01-04-2026", "05-04-2026", "10-04-2026", "15-04-2026", "20-04-2026"],
        "Voucher Type": ["Sales", "Receipt", "Sales", "Sales", "Credit Note"],
        "Voucher No.": ["INV-001", "RCP-001", "INV-002", "INV-003", "CN-001"],
        "Debit": [15000.0, None, 8500.0, 12000.0, None],
        "Credit": [None, 15000.0, None, None, 2000.0],
        "Balance": ["15000 Dr", "0 Dr", "8500 Dr", "20500 Dr", "18500 Dr"],
        "Narration": [
            "Medicines supply", "Payment received", "Surgical supplies",
            "IV fluids", "Return of damaged goods"
        ],
    }
    df = pd.DataFrame(data)
    path = FIXTURE_DIR / "ledger.xlsx"
    df.to_excel(path, index=False)
    print(f"Created: {path}")
    return str(path)


def make_snapshots_fixture() -> pd.DataFrame:
    """
    Returns an in-memory DataFrame of multi-day snapshots for transformer tests.
    Does NOT write to file.
    """
    import numpy as np
    from datetime import date, timedelta

    items = {
        "A00010": {"name": "AMIKACIN", "company": "SHREE CHEHAR",
                   "mrp": 22.50, "purchase_price": 19.39},
        "1801":   {"name": "BIOSAFE GLOVES", "company": "BIOSAFE",
                   "mrp": 2.10,  "purchase_price": 1.73},
        "137":    {"name": "3 BALL SPIROMETER", "company": None,
                   "mrp": 70.25, "purchase_price": 50.0},
        "DEAD01": {"name": "DEAD STOCK ITEM", "company": "XYZ",
                   "mrp": 100.0, "purchase_price": 80.0},
    }

    rows = []
    base_date = date.today()

    for item_code, meta in items.items():
        stock = {"A00010": 2466, "1801": 35800, "137": 34, "DEAD01": 50}.get(item_code, 100)
        daily_sales = {"A00010": 180, "1801": 600, "137": 1, "DEAD01": 0}.get(item_code, 5)

        for i in range(45):  # 45 days of history
            d = base_date - timedelta(days=44 - i)
            sold = daily_sales + np.random.randint(-5, 6) if daily_sales > 0 else 0
            sold = max(0, sold)
            stock = max(0, stock - sold)

            rows.append({
                "item_code": item_code,
                "snapshot_date": d.isoformat(),
                "closing_stock": stock,
                "sales_qty": sold,
                "purchase_price": meta["purchase_price"],
                "mrp": meta["mrp"],
                "company": meta["company"],
                "margin_pct": round((meta["mrp"] - meta["purchase_price"]) / meta["mrp"] * 100, 2),
            })

    return pd.DataFrame(rows)


if __name__ == "__main__":
    make_stock_current_fixture()
    make_stock_sales_fixture()
    make_ledger_fixture()
    print("All fixtures generated.")
