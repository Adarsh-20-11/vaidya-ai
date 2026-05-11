# Connecting Vaidya-AI to Claude Desktop

## Step 1 — Install dependencies

```bash
cd vaidya_mcp
pip install -r requirements.txt
```

## Step 2 — Set up .env

```bash
cp .env.example .env
# Edit .env with your Supabase URL and key
```

## Step 3 — Test the server locally

```bash
mcp dev server.py
```

You should see:
```
Starting Vaidya-AI MCP Server...
Tools: stock_status, item_velocity, ...
Supabase connection OK
```

## Step 4 — Add to Claude Desktop

Find your Claude Desktop config file:
- macOS: ~/Library/Application Support/Claude/claude_desktop_config.json
- Windows: %APPDATA%\Claude\claude_desktop_config.json

Add this to the config (replace paths with your actual paths):

```json
{
  "mcpServers": {
    "vaidya-ai": {
      "command": "python",
      "args": ["/absolute/path/to/vaidya_mcp/server.py"],
      "env": {
        "SUPABASE_URL": "https://your-project.supabase.co",
        "SUPABASE_KEY": "your-service-role-key",
        "ANTHROPIC_API_KEY": "your-key",
        "BUSINESS_NAME": "Magadh Wellness Private Limited",
        "OWNER_NAME": "Papa",
        "LOG_LEVEL": "INFO"
      }
    }
  }
}
```

## Step 5 — Restart Claude Desktop

Claude Desktop will show "vaidya-ai" in the tools panel.

## Step 6 — Test with these prompts

```
"Aaj kya urgent hai?"
"Amikacin ka stock kab khatam hoga?"
"Kaunse items mein margin problem hai?"
"SHREE CHEHAR supplier ka rate trend kya hai?"
"Dead stock mein kitna paisa pada hai?"
```

## Troubleshooting

Server not connecting?
→ Check the absolute path in args[]
→ Check SUPABASE_URL and SUPABASE_KEY are correct
→ Run `mcp dev server.py` manually to see error output

Tools returning empty data?
→ Check pipeline_status resource: "vaidya://pipeline/status"
→ Run the Phase A pipeline first to populate Supabase

Supabase connection error?
→ Make sure you're using the service-role key (not anon key)
→ service-role key is under Supabase → Settings → API
