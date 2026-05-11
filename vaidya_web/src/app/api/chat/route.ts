/**
 * app/api/chat/route.ts
 *
 * Chat API endpoint. Receives messages from the web UI,
 * calls Claude with MCP tools connected via the Anthropic SDK,
 * returns the agent's response.
 *
 * ARCHITECTURE:
 *   Web UI → POST /api/chat → Claude (with MCP tools) → response
 *
 * For Phase B (local dev):
 *   The MCP server runs locally. This API route connects to it
 *   via stdio transport using the Anthropic SDK's MCP client.
 *
 * For Phase D (WhatsApp bridge):
 *   Replace this route with a Twilio webhook handler.
 *   The MCP connection code stays identical — only the input/output changes.
 */

import Anthropic from '@anthropic-ai/sdk';
import { NextRequest, NextResponse } from 'next/server';

const anthropic = new Anthropic({
  apiKey: process.env.ANTHROPIC_API_KEY,
});

// System prompt injected into every chat request
const SYSTEM_PROMPT = `
You are Vaidya-AI, business intelligence assistant for Magadh Wellness 
Private Limited, a pharma and surgical equipment distributor in Gaya, Bihar.

You have access to tools that query the business's Supabase database.
Use them proactively — don't ask for permission to use a tool, just use it.

LANGUAGE: Detect whether the owner writes in Hindi, Hinglish, or English.
Match their language. Default to Hinglish if mixed.

STYLE: Specific and actionable. Use actual numbers. Keep responses concise
(under 250 words for WhatsApp compatibility). Bold key numbers with *asterisks*.

TOOL USAGE ORDER for daily brief:
1. anomalies() — get today's issues
2. stock_status('critical') — critical stock
3. margin_alerts() — profitability issues
Then synthesise.

TOOL USAGE ORDER for item queries:
1. find_item(name) — get item code
2. item_velocity(code) — get sales data
3. supplier_info(code) — get supplier
Then recommend action.

Always end with a clear next step.
`.trim();

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { messages } = body;

    if (!messages || !Array.isArray(messages)) {
      return NextResponse.json(
        { error: 'messages array required' },
        { status: 400 }
      );
    }

    // Connect to local MCP server
    // The server.py must be running: `mcp dev server.py`
    const mcpServerPath = process.env.MCP_SERVER_PATH ||
      `${process.cwd()}/../vaidya_mcp/server.py`;

    let response;
    const toolsUsed: string[] = [];

    try {
      // Use Anthropic SDK with MCP client
      const mcpClient = anthropic.beta.mcp.create({
        transport: {
          type: 'stdio',
          command: 'python',
          args: [mcpServerPath],
          env: {
            SUPABASE_URL: process.env.SUPABASE_URL || '',
            SUPABASE_KEY: process.env.SUPABASE_KEY || '',
            ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY || '',
            BUSINESS_NAME: process.env.BUSINESS_NAME || 'Magadh Wellness',
            OWNER_NAME: process.env.OWNER_NAME || '',
          }
        }
      });

      response = await mcpClient.messages.create({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 1500,
        system: SYSTEM_PROMPT,
        messages: messages.map((m: { role: string; content: string }) => ({
          role: m.role as 'user' | 'assistant',
          content: m.content,
        })),
      });

      // Extract tool names used
      for (const block of response.content) {
        if (block.type === 'tool_use') {
          toolsUsed.push(block.name);
        }
      }

    } catch (mcpError) {
      // MCP connection failed — fall back to direct Claude without tools
      console.warn('MCP connection failed, falling back to direct Claude:', mcpError);

      response = await anthropic.messages.create({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 1000,
        system: SYSTEM_PROMPT + '\n\nNOTE: Database tools are currently unavailable. ' +
                'Tell the user that the MCP server needs to be running locally ' +
                '(run `mcp dev server.py` in the vaidya_mcp directory).',
        messages: messages.map((m: { role: string; content: string }) => ({
          role: m.role as 'user' | 'assistant',
          content: m.content,
        })),
      });
    }

    // Extract text content from response
    const textContent = response.content
      .filter((b: { type: string }) => b.type === 'text')
      .map((b: { type: string; text?: string }) => (b as { type: string; text: string }).text)
      .join('\n');

    return NextResponse.json({
      content: textContent,
      tools_used: [...new Set(toolsUsed)], // deduplicated
      model: response.model,
      usage: response.usage,
    });

  } catch (error) {
    console.error('Chat API error:', error);
    return NextResponse.json(
      {
        content: 'Server error ho gayi. Please try again.',
        error: String(error),
      },
      { status: 500 }
    );
  }
}
