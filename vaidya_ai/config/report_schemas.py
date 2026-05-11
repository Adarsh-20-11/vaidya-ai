"""
config/report_schemas.py

Defines the expected column structure for every Marg Silver Excel export type.
This is the single source of truth for what each report looks like.

WHY THIS EXISTS:
  Marg Silver can change column names between versions or report configurations.
  By centralising expected schemas here, parsers can validate on load and alert
  immediately rather than producing silently corrupt data downstream.

ADDING A NEW REPORT TYPE:
  1. Add a new ReportSchema instance below.
  2. Register it in REPORT_SCHEMAS dict at the bottom.
  3. Create a matching parser in pipeline/parsers/.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict


@dataclass
class ColumnSpec:
    """Defines a single expected column in a report."""
    name: str                          # Exact column name as it appears in Excel
    dtype: str                         # 'str', 'float', 'int', 'date'
    required: bool = True              # If True, parser errors if column missing
    aliases: List[str] = field(default_factory=list)  # Alternative names to try
    nullable: bool = True              # If False, nulls are flagged as anomalies


@dataclass
class ReportSchema:
    """Full schema definition for one Marg report type."""
    report_id: str                     # Internal identifier e.g. 'stock_current'
    display_name: str                  # Human-readable e.g. 'Current Stock Report'
    description: str
    columns: List[ColumnSpec]
    skip_rows: int = 0                 # Rows to skip at top of sheet (headers etc.)
    sheet_name: Optional[str] = None   # None = first sheet
    date_column: Optional[str] = None  # Column to use as report date (if embedded)
    notes: str = ""


# ──────────────────────────────────────────────
# STOCK CURRENT REPORT
# Source: Marg Silver → Reports → Stock Report as on Date
# Columns confirmed from actual export (May 2026)
# ──────────────────────────────────────────────
STOCK_CURRENT_SCHEMA = ReportSchema(
    report_id="stock_current",
    display_name="Current Stock Report",
    description="Point-in-time snapshot of all SKUs with current stock levels, "
                "cost, MRP, purchase price, sales price, company, and rack location.",
    columns=[
        ColumnSpec("code",           "str",   required=True,  aliases=["Code", "CODE"]),
        ColumnSpec("name",           "str",   required=True,  aliases=["Product Name", "ITEM DESCRIPTION", "name"]),
        ColumnSpec("unit",           "str",   required=False, aliases=["Unit", "UNIT"]),
        ColumnSpec("stock",          "float", required=True,  aliases=["Current Stock", "Curr Stock", "CLOSING STOCK"]),
        ColumnSpec("deal",           "float", required=False, aliases=["Deal", "DEAL"]),
        ColumnSpec("free",           "float", required=False, aliases=["Free", "FREE"]),
        ColumnSpec("cost",           "float", required=True,  aliases=["Cost Price", "COST", "cost"]),
        ColumnSpec("value",          "float", required=False, aliases=["Value", "VALUE"]),
        ColumnSpec("mrp",            "float", required=True,  aliases=["M.R.P.", "MRP", "mrp"]),
        ColumnSpec("purchase_price", "float", required=True,  aliases=["Purchase Price", "pur_rate", "Purchase Rate"]),
        ColumnSpec("sales_price",    "float", required=False, aliases=["Sales Price", "rate", "Rate"]),
        ColumnSpec("company",        "str",   required=False, aliases=["Company", "COMPANY"]),
        ColumnSpec("manufacturer",   "str",   required=False, aliases=["Manufacturer", "Manufact", "MANUFACT"]),
        ColumnSpec("rack_no",        "str",   required=False, aliases=["Rack No.", "rackno", "RACK NO"]),
    ],
    notes="Negative stock values are valid (return adjustments). MRP=0 items excluded from margin calc."
)


# ──────────────────────────────────────────────
# STOCK & SALES ANALYSIS REPORT
# Source: Marg Silver → Reports → Stock & Sales Analysis
# Shows opening stock, sales, purchases, closing stock per period
# ──────────────────────────────────────────────
STOCK_SALES_SCHEMA = ReportSchema(
    report_id="stock_sales",
    display_name="Stock & Sales Analysis Report",
    description="Period-based report showing stock movement: opening stock, "
                "sales quantity, purchase quantity, and closing stock per item.",
    columns=[
        ColumnSpec("code",            "str",   required=True,  aliases=["CODE"]),
        ColumnSpec("name",            "str",   required=True,  aliases=["ITEM DESCRIPTION"]),
        ColumnSpec("opening_stock",   "float", required=True,  aliases=["OPENING STOCK", "(1)"]),
        ColumnSpec("sales_qty",       "float", required=True,  aliases=["SALE", "(6)"]),
        ColumnSpec("purchase_qty",    "float", required=True,  aliases=["PURCHASES", "(2)"]),
        ColumnSpec("sales_return",    "float", required=False, aliases=["REPL./ RETURN", "(7)"]),
        ColumnSpec("purchase_return", "float", required=False, aliases=["REPL./ RETURN.1", "(3)"]),
        ColumnSpec("closing_stock",   "float", required=True,  aliases=["CLOSING STOCK", "(5-6+7+8)"]),
        ColumnSpec("rate",            "float", required=False, aliases=["RATE", "STOCK RATE"]),
    ],
    notes="This report requires a date range. The report date is parsed from the PDF/Excel header. "
          "Column names use formula references like (1), (2) in some versions."
)


# ──────────────────────────────────────────────
# PARTY LEDGER REPORT
# Source: Marg Silver → Accounts → Party Ledger
# Shows all transactions for a party (hospital, clinic, etc.)
# ──────────────────────────────────────────────
LEDGER_SCHEMA = ReportSchema(
    report_id="ledger",
    display_name="Party Ledger Report",
    description="Full transaction ledger for a party — invoices raised, "
                "payments received, credit notes, and outstanding balance.",
    columns=[
        ColumnSpec("date",          "date",  required=True,  aliases=["Date", "DATE", "Dt"]),
        ColumnSpec("voucher_type",  "str",   required=False, aliases=["Voucher Type", "Vch Type", "Type"]),
        ColumnSpec("voucher_no",    "str",   required=False, aliases=["Voucher No.", "Vch No.", "Bill No"]),
        ColumnSpec("debit",         "float", required=False, aliases=["Debit", "Dr", "DR"]),
        ColumnSpec("credit",        "float", required=False, aliases=["Credit", "Cr", "CR"]),
        ColumnSpec("balance",       "float", required=False, aliases=["Balance", "Bal", "BAL"]),
        ColumnSpec("narration",     "str",   required=False, aliases=["Narration", "Particulars", "Remarks"]),
        ColumnSpec("party_name",    "str",   required=False, aliases=["Party Name", "Account", "Name"]),
    ],
    notes="Party name may appear only in the header row, not every data row. "
          "Parser must extract party name from header and apply to all rows. "
          "Balance can be Dr (amount owed to Magadh) or Cr (amount owed by Magadh)."
)


# ──────────────────────────────────────────────
# PURCHASE REGISTER
# Source: Marg Silver → Purchase → Purchase Register
# Shows all purchase invoices in a period
# ──────────────────────────────────────────────
PURCHASE_REGISTER_SCHEMA = ReportSchema(
    report_id="purchase_register",
    display_name="Purchase Register",
    description="All purchase invoices raised in a period — vendor, item, "
                "quantity, rate, and GST.",
    columns=[
        ColumnSpec("date",          "date",  required=True,  aliases=["Date", "DATE"]),
        ColumnSpec("vendor_name",   "str",   required=True,  aliases=["Vendor", "Supplier", "Party Name"]),
        ColumnSpec("invoice_no",    "str",   required=False, aliases=["Invoice No", "Bill No", "Voucher No"]),
        ColumnSpec("item_code",     "str",   required=False, aliases=["Item Code", "Code"]),
        ColumnSpec("item_name",     "str",   required=True,  aliases=["Item Name", "Description", "Product"]),
        ColumnSpec("qty",           "float", required=True,  aliases=["Qty", "Quantity", "QTY"]),
        ColumnSpec("rate",          "float", required=True,  aliases=["Rate", "RATE", "Unit Rate"]),
        ColumnSpec("amount",        "float", required=True,  aliases=["Amount", "AMOUNT", "Net Amount"]),
        ColumnSpec("gst_pct",       "float", required=False, aliases=["GST%", "Tax%", "GST Rate"]),
        ColumnSpec("gst_amount",    "float", required=False, aliases=["GST Amount", "Tax Amount"]),
        ColumnSpec("total",         "float", required=False, aliases=["Total", "TOTAL", "Gross Amount"]),
    ],
    notes="GST columns may be split into CGST/SGST/IGST in some versions. "
          "Parser should sum them if split."
)


# ──────────────────────────────────────────────
# SALES REGISTER
# Source: Marg Silver → Sales → Sales Register
# Shows all sales invoices in a period
# ──────────────────────────────────────────────
SALES_REGISTER_SCHEMA = ReportSchema(
    report_id="sales_register",
    display_name="Sales Register",
    description="All sales invoices in a period — customer, item, quantity, "
                "rate, MRP, and GST.",
    columns=[
        ColumnSpec("date",          "date",  required=True,  aliases=["Date", "DATE"]),
        ColumnSpec("customer_name", "str",   required=True,  aliases=["Customer", "Party Name", "Buyer"]),
        ColumnSpec("invoice_no",    "str",   required=False, aliases=["Invoice No", "Bill No"]),
        ColumnSpec("item_code",     "str",   required=False, aliases=["Item Code", "Code"]),
        ColumnSpec("item_name",     "str",   required=True,  aliases=["Item Name", "Description", "Product"]),
        ColumnSpec("qty",           "float", required=True,  aliases=["Qty", "Quantity"]),
        ColumnSpec("rate",          "float", required=True,  aliases=["Rate", "Sale Rate"]),
        ColumnSpec("mrp",           "float", required=False, aliases=["MRP", "M.R.P."]),
        ColumnSpec("amount",        "float", required=True,  aliases=["Amount", "Net Amount"]),
        ColumnSpec("gst_pct",       "float", required=False, aliases=["GST%", "Tax%"]),
        ColumnSpec("gst_amount",    "float", required=False, aliases=["GST Amount", "Tax Amount"]),
        ColumnSpec("total",         "float", required=False, aliases=["Total", "Gross Amount"]),
    ],
    notes="Customer name may be a hospital, clinic, doctor, or retail. "
          "Used for velocity calculation and customer creditworthiness scoring."
)


# ──────────────────────────────────────────────
# ITEM DAY BOOK
# Source: Marg Silver → Reports → Item Day Book
# Shows BOTH sales (SALE) and purchases (PURC) per item per bill
# in a paired-row format (bill header + N item rows).
#
# Unlike other schemas, the columns listed here describe the
# PARSED output, not the raw input. The raw input has 5 generic
# columns; the parser unpacks them into these fields.
# ──────────────────────────────────────────────
ITEM_DAYBOOK_SCHEMA = ReportSchema(
    report_id="item_daybook",
    display_name="Item Day Book",
    description="Per-item record of every sale and purchase line across all bills "
                "in a date range. Single source of truth for transactional data.",
    columns=[
        ColumnSpec("date",             "date",  required=True),
        ColumnSpec("bill_no",          "str",   required=True),
        ColumnSpec("party_name",       "str",   required=True),
        ColumnSpec("city",             "str",   required=False),
        ColumnSpec("transaction_type", "str",   required=True),  # 'SALE' or 'PURC'
        ColumnSpec("item_code",        "str",   required=True),
        ColumnSpec("item_name",        "str",   required=True),
        ColumnSpec("qty",              "float", required=True),
        ColumnSpec("unit_pack",        "str",   required=False),
        ColumnSpec("rate",             "float", required=False),
        ColumnSpec("mrp",              "float", required=False),
        ColumnSpec("company",          "str",   required=False),
        ColumnSpec("amount",           "float", required=False),
        ColumnSpec("batch_no",         "str",   required=False),
        ColumnSpec("expiry",           "str",   required=False),
    ],
    notes="Raw format is paired-row (bill header + N item rows). "
          "Parser handles the multi-row state machine internally. "
          "Output is one row per item transaction. "
          "'SALE' rows feed sales_entries table; 'PURC' rows feed purchase_entries."
)


# ──────────────────────────────────────────────
# REGISTRY — add new schemas here
# ──────────────────────────────────────────────
REPORT_SCHEMAS: Dict[str, ReportSchema] = {
    "stock_current":      STOCK_CURRENT_SCHEMA,
    "stock_sales":        STOCK_SALES_SCHEMA,
    "ledger":             LEDGER_SCHEMA,
    "purchase_register":  PURCHASE_REGISTER_SCHEMA,
    "sales_register":     SALES_REGISTER_SCHEMA,
    "item_daybook":       ITEM_DAYBOOK_SCHEMA,
}


def get_schema(report_id: str) -> ReportSchema:
    if report_id not in REPORT_SCHEMAS:
        raise ValueError(
            f"Unknown report_id '{report_id}'. "
            f"Available: {list(REPORT_SCHEMAS.keys())}"
        )
    return REPORT_SCHEMAS[report_id]
