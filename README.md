# Vaidya-AI

**Intelligent business assistant for Magadh Wellness Private Limited**
*Pharmaceutical & surgical equipment distribution, Gaya, Bihar*

A modular, AI-powered intelligence layer that sits on top of Marg Silver ERP — answering business questions in Hinglish, surfacing critical inventory issues, and helping with vendor coordination via natural conversation.

---

## Repository Structure

```
vaidya-ai/
├── vaidya_ai/          ← Phase A: Data pipeline + tests + schema
├── vaidya_mcp/         ← Phase B: MCP server (agent tools)
├── vaidya_web/         ← Phase B: Next.js chat + dashboard UI
└── README.md           ← You are here
```

Each component is independently deployable. They share one thing: the Supabase database.

---

## How It All Connects

```
┌────────────────────────────────────────────────────────┐
│  MARG SILVER ERP                                       │
│  (Excel export — Stock, Ledger, Sales reports)         │
└──────────────────────┬─────────────────────────────────┘
                       │  daily Excel drop
                       ▼
┌────────────────────────────────────────────────────────┐
│  vaidya_ai/  ── DATA PIPELINE (Phase A)                │
│                                                        │
│  Parsers → Transformers → Loaders → Supabase            │
│                                                        │
│  • base_parser.py     — abstract parser                │
│  • stock_parser.py    — Stock Report                   │
│  • ledger_parser.py   — Party Ledger                   │
│  • sales_parser.py    — Stock & Sales Analysis         │
│  • stock_transformer  — computes intelligence tables   │
│  • supabase_loader    — upserts to cloud               │
│  • run_pipeline.py    — orchestrator (cron-ready)      │
└──────────────────────┬─────────────────────────────────┘
                       │
                       ▼
┌────────────────────────────────────────────────────────┐
│  SUPABASE (PostgreSQL)                                 │
│                                                        │
│  Core:         stock_items, stock_snapshots            │
│  Transactions: party_ledger, purchase, sales           │
│  Intelligence: item_velocity, item_health,             │
│                anomalies_today, supplier_intelligence  │
│  Views:        v_item_dashboard, v_party_outstanding   │
│  System:       pipeline_runs, agent_tool_calls         │
└────────────┬───────────────────────────────────────────┘
             │                              │
             │ MCP tools                    │ direct read
             ▼                              ▼
┌────────────────────────────┐  ┌──────────────────────────┐
│  vaidya_mcp/  (Phase B)    │  │  vaidya_web/  (Phase B)  │
│                            │  │                          │
│  MCP server exposing       │  │  Next.js chat + dashboard │
│  8 tools to AI agents      │  │                          │
│                            │  │  /api/chat → MCP server  │
│  • stock_status            │  │  /api/dashboard → DB     │
│  • item_velocity           │  │                          │
│  • dead_stock              │  │  Chat UI (Hinglish)      │
│  • find_item               │  │  Dashboard with alerts   │
│  • supplier_info           │  │  Pipeline status         │
│  • margin_alerts           │  │                          │
│  • anomalies               │  └──────────────────────────┘
│  • vendor_message_draft    │
│                            │
│  Resources:                │
│  • brief/today             │
│  • pipeline/status         │
│  • schema/summary          │
│                            │
│  Plus: morning_brief.py    │
│  (autonomous 8 AM agent)   │
└────────────────────────────┘
```

---

## Phase Roadmap

| Phase | Status | Description |
|-------|--------|-------------|
| **A** | ✅ Built | Data pipeline, schema, test architecture (45 tests passing) |
| **B** | ✅ Built | MCP server + Web UI + Morning brief agent |
| **C** | Planned | Adhoc 3-agent analytical pipeline (planner → SQL → interpreter) |
| **D** | Planned | Full web app: auth, charts, settings, real-time |
| **E** | Planned | GeM tender engine — daily monitoring + bid recommendations |
| **F** | Planned | Cloud deployment, monitoring, WhatsApp bridge (Twilio) |
| **G** | Planned | MBK file exploration / Marg API decision point |
| **H** | Planned | Android APK with Hindi/Maghi voice — depends on Phase G |

---

## Quick Start (Development)

### 1. Set up Supabase
- Create a project at supabase.com
- Run `vaidya_ai/schema/migrations.sql` in the SQL editor
- Get your project URL and service-role key

### 2. Run the data pipeline
```bash
cd vaidya_ai
pip install -r requirements.txt
cp .env.example .env  # Fill in Supabase credentials
python run_pipeline.py --report stock --file path/to/stock_export.xlsx
```

### 3. Run the MCP server
```bash
cd vaidya_mcp
pip install -r requirements.txt
cp .env.example .env  # Fill in credentials
mcp dev server.py     # Local dev mode
```

### 4. Run the web UI
```bash
cd vaidya_web
npm install
cp .env.example .env.local  # Fill in credentials including MCP_SERVER_PATH
npm run dev
```
#Still under development, hence localhost link for now. Placeholder.
Open http://localhost:3000

### 5. (Optional) Connect Claude Desktop
See `vaidya_mcp/CLAUDE_DESKTOP_SETUP.md` for adding Vaidya-AI as an MCP server to Claude Desktop. This lets you test tools without the web UI.

---

## Testing

```bash
cd vaidya_ai
python -m unittest discover tests -v
```

Currently: **45 tests, all passing**

- 18 parser tests (stock parser, edge cases, schema validation)
- 20 transformer tests (velocity, health, anomalies, supplier intel)
- 7 integration tests (full pipeline, idempotency, file types)

---

## Design Principles

1. **Marg stays untouched.** All reads are via Excel export. We never write to Marg in Phase A–F.
2. **AI is the reasoning layer, not the data layer.** Supabase is memory. The agent does the thinking.
3. **One MCP server, infinite clients.** Web, future WhatsApp bridge, future Android APK — all talk to the same MCP server.
4. **Append-only snapshots.** History is sacred. Never update past data.
5. **Honest confidence.** When data is insufficient, the agent says so — never fabricates.
6. **Owner-in-the-loop.** No order is sent without explicit human approval.

---

## License & Authorship

Internal project for **Magadh Wellness Private Limited**, Gaya, Bihar.
GSTIN: 10AAQCM7077G1ZA
