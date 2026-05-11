# Vaidya-AI Web UI

Next.js web interface for Vaidya-AI. Chat with the agent and view stock intelligence dashboard.

## Setup

```bash
# Install dependencies
npm install

# Copy env template and fill in values
cp .env.example .env.local

# Run dev server
npm run dev
```

Open http://localhost:3000

## Architecture

```
Browser → Next.js Web UI
            ├── /api/chat      → Anthropic SDK + MCP server → Supabase
            └── /api/dashboard → Supabase (direct read)
```

The chat route connects to the local MCP server (`vaidya_mcp/server.py`) via
the Anthropic SDK's MCP client. The dashboard route reads Supabase directly
for fast, static data.

## Phases

This UI is the primary client for Phases B–F. In Phase E, it gets enhanced with:
- Real-time updates (Supabase Realtime)
- Settings panel (reorder thresholds, suppliers)
- Charts (Recharts integration)
- WhatsApp bridge handoff

In Phase H, a parallel Android APK takes over for warehouse workers,
but the web UI remains the primary owner interface.
