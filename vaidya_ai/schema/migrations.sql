-- =============================================================================
-- VAIDYA-AI SUPABASE SCHEMA
-- Magadh Wellness Private Limited
-- Version: 1.0 | May 2026
--
-- Run this file once against your Supabase project to create all tables.
-- Safe to re-run (uses IF NOT EXISTS / CREATE OR REPLACE).
--
-- TABLE HIERARCHY:
--   Core:         stock_items, stock_snapshots
--   Transactions: party_ledger_entries, purchase_entries, sales_entries
--   Intelligence: item_velocity, item_health, anomalies_today, supplier_intelligence
--   System:       pipeline_runs, agent_tool_calls
-- =============================================================================


-- =============================================================================
-- CORE TABLES
-- =============================================================================

-- Master SKU list. One row per product code.
-- Updated on every stock sync but never deleted (historical reference).
CREATE TABLE IF NOT EXISTS stock_items (
    id                  BIGSERIAL PRIMARY KEY,
    code                TEXT NOT NULL UNIQUE,       -- Marg item code (e.g. "137", "A00010")
    name                TEXT NOT NULL,              -- Product name
    unit                TEXT,                       -- PCS, BOX, AMP, VIA, TAB, etc.
    category            TEXT,                       -- 'pharma' | 'surgical' | 'consumable' | 'equipment'
    mrp                 NUMERIC(12, 2),             -- Maximum retail price
    company             TEXT,                       -- Supplier/distributor name (nullable = unknown)
    manufacturer        TEXT,                       -- Actual manufacturer
    rack_no             TEXT,                       -- Physical storage location
    reorder_level       INTEGER DEFAULT 10,         -- Configurable threshold (set manually per item)
    is_active           BOOLEAN DEFAULT TRUE,       -- Set to FALSE if item discontinued
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE stock_items IS 'Master product catalogue. One row per SKU. Source: Marg Silver stock export.';
COMMENT ON COLUMN stock_items.code IS 'Marg item code — primary business identifier. May be numeric or alphanumeric.';
COMMENT ON COLUMN stock_items.reorder_level IS 'Manually configured. Default 10. Used by item_health to classify urgency.';


-- Daily stock snapshots. Append-only — never updated, only inserted.
-- One row per item per date. This is the foundation for all intelligence.
CREATE TABLE IF NOT EXISTS stock_snapshots (
    id                  BIGSERIAL PRIMARY KEY,
    item_code           TEXT NOT NULL REFERENCES stock_items(code) ON DELETE RESTRICT,
    snapshot_date       DATE NOT NULL,
    closing_stock       NUMERIC(12, 2),             -- Stock at end of day
    opening_stock       NUMERIC(12, 2),             -- Stock at start of day (from sales analysis report)
    sales_qty           NUMERIC(12, 2) DEFAULT 0,   -- Units sold in period
    purchase_qty        NUMERIC(12, 2) DEFAULT 0,   -- Units purchased in period
    net_movement        NUMERIC(12, 2),             -- purchase_qty - sales_qty
    purchase_price      NUMERIC(12, 2),             -- Landed cost per unit
    sales_price         NUMERIC(12, 2),             -- Selling price per unit
    cost                NUMERIC(12, 2),             -- Cost value (may differ from purchase_price)
    value               NUMERIC(14, 2),             -- Total inventory value (stock × cost)
    margin_pct          NUMERIC(6, 2),              -- (mrp - purchase_price) / mrp * 100
    is_negative_stock   BOOLEAN DEFAULT FALSE,      -- Flag: stock < 0
    supplier_unknown    BOOLEAN DEFAULT FALSE,      -- Flag: company was -BLANK-
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (item_code, snapshot_date)               -- Upsert key
);

CREATE INDEX IF NOT EXISTS idx_snapshots_item_date
    ON stock_snapshots (item_code, snapshot_date DESC);

CREATE INDEX IF NOT EXISTS idx_snapshots_date
    ON stock_snapshots (snapshot_date DESC);

COMMENT ON TABLE stock_snapshots IS 'Append-only daily stock records. Never update — only insert. Source of all velocity and trend computation.';
COMMENT ON COLUMN stock_snapshots.is_negative_stock IS 'Negative stock usually means a return was processed before the purchase. Flag and investigate in Marg.';


-- =============================================================================
-- TRANSACTION TABLES
-- =============================================================================

-- Party ledger: all financial transactions per customer/vendor
CREATE TABLE IF NOT EXISTS party_ledger_entries (
    id                  BIGSERIAL PRIMARY KEY,
    party_name          TEXT NOT NULL,              -- Hospital, clinic, doctor, or vendor name
    party_type          TEXT,                       -- 'customer' | 'vendor' | 'unknown'
    date                DATE NOT NULL,
    voucher_type        TEXT,                       -- Sales Invoice, Receipt, Credit Note, etc.
    voucher_no          TEXT,                       -- Invoice/receipt number
    debit               NUMERIC(14, 2),             -- Amount Dr (party owes Magadh)
    credit              NUMERIC(14, 2),             -- Amount Cr (Magadh owes party)
    net_amount          NUMERIC(14, 2),             -- debit - credit
    balance             NUMERIC(14, 2),             -- Running balance
    balance_type        TEXT,                       -- 'Dr' | 'Cr'
    narration           TEXT,                       -- Transaction description
    days_outstanding    INTEGER,                    -- Days since transaction date
    is_overdue          BOOLEAN DEFAULT FALSE,      -- True if > credit_days old with debit balance
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (party_name, date, voucher_no)
);

CREATE INDEX IF NOT EXISTS idx_ledger_party
    ON party_ledger_entries (party_name, date DESC);

CREATE INDEX IF NOT EXISTS idx_ledger_overdue
    ON party_ledger_entries (is_overdue, date DESC)
    WHERE is_overdue = TRUE;

COMMENT ON TABLE party_ledger_entries IS 'Financial transactions per party. Used for creditworthiness analysis and outstanding tracking.';


-- Purchase register: all items purchased from vendors
CREATE TABLE IF NOT EXISTS purchase_entries (
    id                  BIGSERIAL PRIMARY KEY,
    date                DATE NOT NULL,
    vendor_name         TEXT NOT NULL,
    invoice_no          TEXT,
    item_code           TEXT,                       -- FK to stock_items (nullable — may not always match)
    item_name           TEXT NOT NULL,
    qty                 NUMERIC(12, 2) NOT NULL,
    rate                NUMERIC(12, 2) NOT NULL,    -- Per unit purchase rate
    amount              NUMERIC(14, 2),             -- actual billed amount
    discount_pct        NUMERIC(6, 2),              -- ((qty*rate - amount) / (qty*rate)) × 100
    category            TEXT DEFAULT 'purchase',    -- purchase | stock_in | purchase_return
    batch_no            TEXT DEFAULT '',            -- '' (not NULL) so unique key works
    expiry              TEXT,
    gst_pct             NUMERIC(6, 2),
    gst_amount          NUMERIC(14, 2),
    total               NUMERIC(14, 2),             -- amount + gst_amount
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    -- One invoice can have the same item across multiple batches.
    -- batch_no is part of the key (with '' fallback) so each line is unique.
    UNIQUE (invoice_no, item_code, date, batch_no)
);

CREATE INDEX IF NOT EXISTS idx_purchase_vendor_date
    ON purchase_entries (vendor_name, date DESC);

CREATE INDEX IF NOT EXISTS idx_purchase_item
    ON purchase_entries (item_code, date DESC);

CREATE INDEX IF NOT EXISTS idx_purchase_category
    ON purchase_entries (category, date DESC);


-- Sales register: all items sold to customers
-- Includes retail SALE, wholesale stock loans (ST.L), and returns
CREATE TABLE IF NOT EXISTS sales_entries (
    id                  BIGSERIAL PRIMARY KEY,
    date                DATE NOT NULL,
    customer_name       TEXT NOT NULL,
    invoice_no          TEXT,
    item_code           TEXT,
    item_name           TEXT NOT NULL,
    qty                 NUMERIC(12, 2) NOT NULL,
    rate                NUMERIC(12, 2) NOT NULL,
    mrp                 NUMERIC(12, 2),
    amount              NUMERIC(14, 2),             -- actual billed amount
    discount_pct        NUMERIC(6, 2),              -- ((qty*rate - amount) / (qty*rate)) × 100
    category            TEXT DEFAULT 'retail',      -- retail | wholesale | sales_return | replacement
    batch_no            TEXT DEFAULT '',            -- '' (not NULL) so unique key works
    expiry              TEXT,
    gst_pct             NUMERIC(6, 2),
    gst_amount          NUMERIC(14, 2),
    total               NUMERIC(14, 2),
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    -- One invoice can have the same item across multiple batches.
    -- batch_no is part of the key (with '' fallback) so each line is unique.
    UNIQUE (invoice_no, item_code, date, batch_no)
);

CREATE INDEX IF NOT EXISTS idx_sales_customer_date
    ON sales_entries (customer_name, date DESC);

CREATE INDEX IF NOT EXISTS idx_sales_item
    ON sales_entries (item_code, date DESC);

CREATE INDEX IF NOT EXISTS idx_sales_category
    ON sales_entries (category, date DESC);


-- ─────────────────────────────────────────────────────────────────────
-- MIGRATIONS FOR EXISTING INSTALLATIONS
-- ─────────────────────────────────────────────────────────────────────
-- If you already created sales_entries / purchase_entries WITHOUT the newer
-- columns, run these to add them:
--
--   ALTER TABLE sales_entries     ADD COLUMN IF NOT EXISTS category     TEXT DEFAULT 'retail';
--   ALTER TABLE purchase_entries  ADD COLUMN IF NOT EXISTS category     TEXT DEFAULT 'purchase';
--   ALTER TABLE sales_entries     ADD COLUMN IF NOT EXISTS discount_pct NUMERIC(6, 2);
--   ALTER TABLE purchase_entries  ADD COLUMN IF NOT EXISTS discount_pct NUMERIC(6, 2);
--
-- The batch_no migration is more involved because the unique constraint changes.
-- Run these together if you've already created the tables:
--
--   ALTER TABLE sales_entries     ADD COLUMN IF NOT EXISTS batch_no TEXT DEFAULT '';
--   ALTER TABLE purchase_entries  ADD COLUMN IF NOT EXISTS batch_no TEXT DEFAULT '';
--   ALTER TABLE sales_entries     ADD COLUMN IF NOT EXISTS expiry   TEXT;
--   ALTER TABLE purchase_entries  ADD COLUMN IF NOT EXISTS expiry   TEXT;
--
--   -- Backfill empty string for any NULL batch_no rows already loaded
--   UPDATE sales_entries     SET batch_no = '' WHERE batch_no IS NULL;
--   UPDATE purchase_entries  SET batch_no = '' WHERE batch_no IS NULL;
--
--   -- Drop the old unique constraint and add the new one
--   ALTER TABLE sales_entries     DROP CONSTRAINT IF EXISTS sales_entries_invoice_no_item_code_date_key;
--   ALTER TABLE purchase_entries  DROP CONSTRAINT IF EXISTS purchase_entries_invoice_no_item_code_date_key;
--   ALTER TABLE sales_entries     ADD CONSTRAINT sales_entries_unique
--     UNIQUE (invoice_no, item_code, date, batch_no);
--   ALTER TABLE purchase_entries  ADD CONSTRAINT purchase_entries_unique
--     UNIQUE (invoice_no, item_code, date, batch_no);


-- =============================================================================
-- INTELLIGENCE TABLES
-- Computed nightly by the transformer. Read by the AI agent via tools.
-- =============================================================================

-- Velocity: average daily sales across time windows
CREATE TABLE IF NOT EXISTS item_velocity (
    id                      BIGSERIAL PRIMARY KEY,
    item_code               TEXT NOT NULL REFERENCES stock_items(code),
    avg_daily_sales_7d      NUMERIC(10, 2),         -- NULL if insufficient data
    avg_daily_sales_30d     NUMERIC(10, 2),
    avg_daily_sales_90d     NUMERIC(10, 2),
    confidence_7d           TEXT,                   -- 'reliable' | 'limited' | 'insufficient_data'
    confidence_30d          TEXT,
    confidence_90d          TEXT,
    velocity_trend          TEXT,                   -- 'accelerating' | 'stable' | 'slowing' | 'unknown'
    computed_at             TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (item_code)                              -- Latest computation only
);

COMMENT ON TABLE item_velocity IS 'Nightly computed. Average daily sales per item. NULL = insufficient snapshot history.';
COMMENT ON COLUMN item_velocity.confidence_7d IS 'reliable = ≥20 snapshots. limited = ≥7. insufficient_data = <7.';


-- Health: current status of each item
CREATE TABLE IF NOT EXISTS item_health (
    id                  BIGSERIAL PRIMARY KEY,
    item_code           TEXT NOT NULL REFERENCES stock_items(code),
    computed_date       DATE NOT NULL,
    closing_stock       NUMERIC(12, 2),
    days_remaining      NUMERIC(8, 1),              -- Stock / avg_daily_sales_30d. NULL if no velocity.
    reorder_urgency     TEXT NOT NULL,              -- 'critical' | 'watch' | 'ok' | 'unknown' | 'anomaly'
    margin_pct          NUMERIC(6, 2),
    margin_status       TEXT,                       -- 'critical' | 'watch' | 'ok' | 'unknown'
    last_supplier       TEXT,
    UNIQUE (item_code, computed_date)
);

CREATE INDEX IF NOT EXISTS idx_health_urgency
    ON item_health (reorder_urgency, computed_date DESC);

COMMENT ON TABLE item_health IS 'Daily computed health status per item. Primary table for agent get_stock_status tool.';


-- Anomalies: items flagged for attention today
CREATE TABLE IF NOT EXISTS anomalies_today (
    id                  BIGSERIAL PRIMARY KEY,
    item_code           TEXT NOT NULL,
    item_name           TEXT,
    anomaly_type        TEXT NOT NULL,              -- See ANOMALY TYPES below
    severity            TEXT NOT NULL,              -- 'critical' | 'high' | 'medium' | 'low'
    detail              TEXT,                       -- Human-readable explanation
    detected_date       DATE NOT NULL DEFAULT CURRENT_DATE,
    resolved            BOOLEAN DEFAULT FALSE,      -- Manually mark resolved
    resolved_at         TIMESTAMPTZ,
    UNIQUE (item_code, anomaly_type, detected_date)
);

-- ANOMALY TYPES:
--   negative_stock    Stock quantity is negative (return/adjustment issue)
--   critical_stock    Days remaining ≤ 14 at current velocity
--   margin_erosion    Margin % below critical threshold
--   dead_stock        No sales in 90 days (capital locked)
--   velocity_spike    Sales suddenly 30% above 30d average
--   supplier_gap      No supplier mapped (can't reorder)

CREATE INDEX IF NOT EXISTS idx_anomalies_date_severity
    ON anomalies_today (detected_date DESC, severity);

COMMENT ON TABLE anomalies_today IS 'Nightly computed anomalies. Cleared and recomputed each night. agent get_anomalies tool reads this.';


-- Supplier intelligence: rate trends per supplier
CREATE TABLE IF NOT EXISTS supplier_intelligence (
    id                  BIGSERIAL PRIMARY KEY,
    supplier            TEXT NOT NULL,
    avg_rate_30d        NUMERIC(12, 2),
    avg_rate_90d        NUMERIC(12, 2),
    rate_trend          TEXT,                       -- 'increasing' | 'stable' | 'decreasing' | 'unknown'
    items_supplied      INTEGER,                    -- Count of distinct SKUs from this supplier
    computed_date       DATE NOT NULL DEFAULT CURRENT_DATE,
    UNIQUE (supplier, computed_date)
);

COMMENT ON TABLE supplier_intelligence IS 'Monthly computed supplier rate trends. Used by agent for reorder recommendations.';


-- =============================================================================
-- SYSTEM TABLES
-- =============================================================================

-- Pipeline audit log: every run is recorded
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id                  BIGSERIAL PRIMARY KEY,
    report_id           TEXT NOT NULL,              -- 'stock_current' | 'ledger' | 'stock_sales' etc.
    file_path           TEXT,
    file_hash           TEXT,                       -- MD5 of input file (duplicate detection)
    row_count           INTEGER,
    success             BOOLEAN NOT NULL,
    errors              JSONB DEFAULT '[]',
    warnings            JSONB DEFAULT '[]',
    ran_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_report_date
    ON pipeline_runs (report_id, ran_at DESC);

COMMENT ON TABLE pipeline_runs IS 'Immutable audit log of every pipeline execution. Used for debugging and dead-man switch detection.';


-- Agent tool calls: every AI agent query is logged
CREATE TABLE IF NOT EXISTS agent_tool_calls (
    id                  BIGSERIAL PRIMARY KEY,
    session_id          TEXT,                       -- WhatsApp session identifier
    question            TEXT,                       -- User's original question
    tools_called        JSONB,                      -- Array of {tool, params, result_summary}
    response_summary    TEXT,                       -- First 500 chars of agent response
    latency_ms          INTEGER,                    -- Total response time
    called_at           TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE agent_tool_calls IS 'Audit log for AI agent interactions. Used for debugging, latency monitoring, and identifying common query patterns.';


-- =============================================================================
-- VIEWS (convenience — used by agent tools)
-- =============================================================================

-- Current state of all items: latest health + velocity in one place
CREATE OR REPLACE VIEW v_item_dashboard AS
SELECT
    si.code,
    si.name,
    si.unit,
    si.category,
    si.company         AS default_supplier,
    si.mrp,
    si.reorder_level,
    ih.closing_stock,
    ih.days_remaining,
    ih.reorder_urgency,
    ih.margin_pct,
    ih.margin_status,
    iv.avg_daily_sales_7d,
    iv.avg_daily_sales_30d,
    iv.velocity_trend,
    iv.confidence_30d
FROM stock_items si
LEFT JOIN item_health ih
    ON si.code = ih.item_code
    AND ih.computed_date = CURRENT_DATE
LEFT JOIN item_velocity iv
    ON si.code = iv.item_code
WHERE si.is_active = TRUE;

COMMENT ON VIEW v_item_dashboard IS 'Agent primary view. Joins stock_items + item_health + item_velocity into a single queryable surface.';


-- Outstanding by party: summarised ledger position
CREATE OR REPLACE VIEW v_party_outstanding AS
SELECT
    party_name,
    SUM(CASE WHEN balance_type = 'Dr' THEN balance ELSE 0 END) AS total_outstanding_dr,
    SUM(CASE WHEN balance_type = 'Cr' THEN balance ELSE 0 END) AS total_credit_cr,
    COUNT(*) FILTER (WHERE is_overdue = TRUE)                   AS overdue_invoices,
    MAX(date)                                                   AS last_transaction_date,
    MIN(date) FILTER (WHERE is_overdue = TRUE)                  AS oldest_overdue_date
FROM party_ledger_entries
GROUP BY party_name;

COMMENT ON VIEW v_party_outstanding IS 'Party-level outstanding summary. Used by agent for ledger queries.';


-- =============================================================================
-- ROW LEVEL SECURITY (enable in Supabase dashboard)
-- Uncomment when moving to production
-- =============================================================================

-- ALTER TABLE stock_items ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE stock_snapshots ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE party_ledger_entries ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE agent_tool_calls ENABLE ROW LEVEL SECURITY;