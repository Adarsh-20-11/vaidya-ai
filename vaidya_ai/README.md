# Vaidya-AI Data Pipeline

Modular data pipeline for Magadh Wellness Pvt. Ltd.
Ingests Marg Silver Excel/CSV exports → cleans → transforms → loads into Supabase.

## Structure

```
vaidya_ai/
├── config/
│   ├── settings.py          # Central config (env vars, thresholds, cities)
│   └── report_schemas.py    # Expected column definitions per report type
├── pipeline/
│   ├── parsers/             # One parser per Marg report type
│   │   ├── base_parser.py     # Abstract base — all parsers extend this
│   │   ├── stock_parser.py    # Stock Report (current stock, rates, suppliers)
│   │   ├── ledger_parser.py   # Party Ledger (outstanding, payments)
│   │   ├── sales_parser.py    # Stock & Sales Analysis (period totals)
│   │   └── daybook_parser.py  # Item Day Book (per-line SALE + PURC) ⭐
│   ├── transformers/        # Business logic on top of parsed data
│   │   ├── stock_transformer.py    # Velocity, health, anomalies
│   │   └── daybook_transformer.py  # Splits daybook → sales + purchase tables
│   └── loaders/
│       ├── supabase_loader.py  # Upsert to Supabase tables
│       └── local_loader.py     # Save to local CSV (dry-run / dev)
├── tests/
│   ├── fixtures/            # Sample Excel/CSV files for tests
│   ├── unit/                # One test file per module
│   └── integration/         # End-to-end pipeline tests
├── schema/
│   └── migrations.sql       # Full Supabase schema (run once in Supabase)
├── run_pipeline.py          # Main entry point (called by cron)
└── requirements.txt
```

## Reports Supported

| Report ID | Source in Marg | Format | What it gives you |
|-----------|----------------|--------|-------------------|
| `stock_current` | Reports → Stock Report as on Date | XLSX | Point-in-time stock per SKU |
| `stock_sales` | Reports → Stock & Sales Analysis | XLSX | Period totals (sale/purc qty per item) |
| `item_daybook` ⭐ | Reports → Item Day Book | CSV | Per-line transactional history |
| `ledger` | Accounts → Party Ledger | XLSX | Party-wise transactions |

The **Item Day Book** is the single most valuable export — it gives you
every SALE and PURC line across all bills in a date range. With 30+ days
of daybook data, the agent has real velocity and customer behaviour from
day one (no waiting for snapshots to accumulate).

## Adding a New Report Type

1. Add expected columns to `config/report_schemas.py`
2. Create `pipeline/parsers/your_report_parser.py` extending `BaseParser`
   - Override `_post_process()` for flat tabular reports
   - Override `parse()` if format is non-tabular (like daybook's paired rows)
3. Create `pipeline/transformers/your_report_transformer.py` if needed
4. Add a `run_xxx()` function in `run_pipeline.py` and wire to CLI
5. Add fixture in `tests/fixtures/generate_fixtures.py`
6. Add unit + integration tests

## Running

```bash
# Full pipeline (auto-discovers files in ./exports/)
python run_pipeline.py --report all

# Specific report
python run_pipeline.py --report stock
python run_pipeline.py --report daybook
python run_pipeline.py --report ledger

# Specific file
python run_pipeline.py --report daybook --file ./exports/april_daybook.csv

# Dry run — parse + transform, save to local CSVs, no Supabase writes
python run_pipeline.py --report all --dry-run

# Historical backfill (specify the date the report covers)
python run_pipeline.py --report daybook --file ./historical/apr_2026.csv --date 2026-04-30
```

## Testing

```bash
# Full test suite (81 tests)
python -m unittest discover tests -v

# Single module
python -m unittest tests.unit.test_daybook_parser -v
```

## File Discovery Convention

The pipeline auto-discovers files in `./exports/` (configurable via
`MARG_EXPORT_DIR` env var) by filename pattern:

- `*stock*.xlsx`   → stock_current
- `*sales*.xlsx`   → stock_sales
- `*ledger*.xlsx`  → ledger
- `*daybook*.csv`  → item_daybook

So if you save your daily Marg exports as `stock_20260510.xlsx` and
`daybook_april.csv`, the pipeline picks them up automatically.
