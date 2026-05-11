# Vaidya-AI Data Pipeline

Modular data pipeline for Magadh Wellness Pvt. Ltd.
Ingests Marg Silver Excel exports → cleans → transforms → loads into Supabase.

## Structure

```
vaidya_ai/
├── config/
│   ├── settings.py          # Central config (env vars, thresholds)
│   └── report_schemas.py    # Expected column definitions per report type
├── pipeline/
│   ├── parsers/             # One parser per Marg report type
│   │   ├── base_parser.py   # Abstract base — all parsers extend this
│   │   ├── stock_parser.py  # Stock Report (current stock, rates, suppliers)
│   │   ├── ledger_parser.py # Party Ledger (outstanding, payments)
│   │   └── sales_parser.py  # Sales Analysis (movement, velocity)
│   ├── transformers/        # Business logic on top of parsed data
│   │   ├── stock_transformer.py
│   │   ├── ledger_transformer.py
│   │   └── intelligence_transformer.py  # Velocity, health, anomaly computation
│   └── loaders/
│       ├── supabase_loader.py  # Upsert to Supabase tables
│       └── local_loader.py     # Save to local CSV (fallback / dev)
├── tests/
│   ├── fixtures/            # Sample Excel files (anonymised) for tests
│   ├── unit/                # One test file per module
│   └── integration/         # End-to-end pipeline tests
├── schema/
│   └── migrations.sql       # Full Supabase schema
├── run_pipeline.py          # Main entry point (called by cron)
└── requirements.txt
```

## Adding a New Report Type

1. Add expected columns to `config/report_schemas.py`
2. Create `pipeline/parsers/your_report_parser.py` extending `BaseParser`
3. Create `pipeline/transformers/your_report_transformer.py` if needed
4. Add a loader call in `run_pipeline.py`
5. Add fixture file + tests in `tests/`

That's it. The base classes handle validation, error logging, and dead-man switch alerts.

## Running

```bash
# Full pipeline
python run_pipeline.py --report all

# Single report
python run_pipeline.py --report stock
python run_pipeline.py --report ledger
python run_pipeline.py --report sales

# Dry run (parse + transform, no Supabase write)
python run_pipeline.py --report all --dry-run

# Run tests
python -m pytest tests/ -v --cov=pipeline
```
