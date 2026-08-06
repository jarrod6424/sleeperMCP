"""
sleeper_core — the data layer behind the Sleeper MCP server.

Plain Python. No MCP imports anywhere in this package, by design.

Two consumers depend on that rule:

  server.py    wraps these functions as @mcp.tool() definitions for Claude.
  app_backend  imports them directly, skipping the MCP protocol entirely.

The second consumer is the reason this package exists. MCP is a protocol for
exposing tools to a language model: JSON-RPC, a session handshake, results
shaped for an LLM to read. During a live draft, going app -> HTTPS -> MCP
session -> JSON-RPC just to look up a player is a lot of ceremony for a dict
lookup. In-process, it is a function call.

Rule while working in here: sleeper_core NEVER imports from server. If you
find yourself wanting to, the thing you want is in the wrong module.
"""
