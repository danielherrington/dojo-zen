---
name: dojo-mcp-and-web-portal
description: >-
  Guides the Dojo Zen FastAPI Web Portal (src/dojo/server.py, src/dojo/web/index.html),
  remote Streamable HTTP & local stdio MCP server (src/dojo/mcp_server.py), grounded
  classroom Q&A engine (src/dojo/qa.py), and Twilio SMS webhook (src/dojo/sms.py).
  Use when adding or modifying MCP tools/resources, web endpoints, UI tabs, or Q&A search.
---

# Dojo Zen MCP Server & Web Portal Skill

## Overview

[`src/dojo/server.py`](../../src/dojo/server.py) runs a single FastAPI application that serves:
1. **Family Web Portal** (`GET /`, `GET /api/briefing`, `GET /api/feed`, `GET /api/feed/{item_id}`, `POST /api/ask`, `POST /api/sync`, `GET /api/status`)
2. **Remote Streamable HTTP MCP Server** (`POST/GET /mcp`)
3. **Cloud Scheduler Cron Webhooks** (`POST /api/cron/check-alerts`, `POST /api/cron/daily-digest`)
4. **Twilio SMS / WhatsApp Webhook** (`POST /api/sms/webhook`)

## Streamable HTTP MCP Architecture (`src/dojo/mcp_server.py` & `src/dojo/server.py`)

- [`mcp_server.py`](../../src/dojo/mcp_server.py) defines `mcp = MCPServer(name="dojo-zen", ...)` from `mcp.server.mcpserver`.
- [`server.py`](../../src/dojo/server.py) injects the active database (`set_database(db)`) so the MCP server uses **Firestore** in production and **SQLite** in local development/tests.
- Mounted at `/mcp` with `TransportSecuritySettings(enable_dns_rebinding_protection=False)` so remote MCP clients can connect to `https://dojo.herrington.ai/mcp` without host header rejection:
  ```python
  mcp_subapp = mcp_server.streamable_http_app(
      streamable_http_path="/",
      transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
  )

  @asynccontextmanager
  async def lifespan(app: FastAPI):
      async with mcp_server._lowlevel_server._session_manager.run():
          yield
  ```
- Both `Route("/mcp", endpoint=mcp_subapp.routes[0].endpoint)` and `app.mount("/mcp", mcp_subapp)` are registered so `/mcp` works with or without a trailing slash.

## Unified Feed & Class Updates Parity

Both the Web Portal (`GET /api/feed` and `GET /api/feed/{item_id}`) and MCP tools (`get_recent_feed`, `search_feed`, `get_feed_item`, `get_action_items`, `get_upcoming_dates`, `get_daily_briefing`) merge **Story Feed posts** (`feed_items`) and **Class Updates / Teacher Messages** (`messages`), sorted newest-first.

## Testing the MCP Server Over HTTP

Run the end-to-end MCP client test in [`tests/test_mcp_server.py`](../../tests/test_mcp_server.py):
```bash
uv run pytest tests/test_mcp_server.py
```
Or test against live production (`https://dojo.herrington.ai/mcp`):
```bash
PYTHONPATH=src uv run python -c "
import anyio
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    async with streamablehttp_client('https://dojo.herrington.ai/mcp') as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = await session.list_tools()
            print('Tools:', [t.name for t in tools.tools])
            res = await session.call_tool('get_daily_briefing', {})
            print(res.content[0].text[:400])

anyio.run(main)
"
```
