#!/usr/bin/env python3
"""Grok MCP server (stdio) — lets other coding agents ask Grok.

Small on purpose: Python standard library only, no pip installs, one file.
Transport: newline-delimited JSON-RPC 2.0 over stdio (the MCP stdio transport).

Tool:
  ask_grok(prompt, model="grok-4.7", system=None)
    Asks Grok a question via the xAI Responses API and returns its answer text.

Auth:
  The server reads the xAI API key from the XAI_API_KEY environment variable
  at call time. It never stores, logs, or prints the key. Get a key at
  https://console.x.ai (API Keys page) and add prepaid credits under Billing.
  A Grok / X subscription does NOT fund API calls; API billing is separate.

xAI API (verified against https://docs.x.ai/docs/guides/chat, 2026-11-12):
  POST https://api.x.ai/v1/responses
  Headers: Authorization: Bearer <key>, Content-Type: application/json
  Body: {"model": "grok-4.7",
         "input": [{"role": "system", "content": ...},
                   {"role": "user", "content": ...}],
         "store": false}
  The answer text lives in response["output"] -> message items ->
  content parts with type "output_text" and a "text" field.
  "store": false keeps the request/response off xAI's servers.

Usage (Windows, in PowerShell):
  $env:XAI_API_KEY = "xai-..."          # once per session, or set it as a
                                          # Windows user environment variable
  python grok_mcp_server.py

Clients point at this file as an MCP server command; see README.md for the
Codex and Antigravity config snippets.
"""

import json
import os
import sys
import urllib.error
import urllib.request

SERVER_NAME = "grok-mcp"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

XAI_API_URL = "https://api.x.ai/v1/responses"
DEFAULT_MODEL = "grok-4.7"
DEFAULT_SYSTEM = (
    "You are Grok, an AI assistant. Another coding agent is consulting you. "
    "Answer directly and concretely: give the answer, the reasoning in brief, "
    "and nothing else. If you do not know something, say so instead of guessing."
)
HTTP_TIMEOUT_SECONDS = 300


def log(message):
    """Diagnostics go to stderr only. Stdout is the MCP protocol stream."""
    print(message, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# xAI Responses API
# --------------------------------------------------------------------------

def extract_text(payload):
    """Pull the assistant's answer text out of a Responses API payload."""
    if isinstance(payload.get("output_text"), str) and payload["output_text"]:
        return payload["output_text"]
    parts = []
    for item in payload.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for part in item.get("content", []) or []:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    text = part.get("text", "")
                    if text:
                        parts.append(text)
        elif item.get("type") == "output_text" and item.get("text"):
            parts.append(item["text"])
    return "".join(parts)


def ask_grok(prompt, model=DEFAULT_MODEL, system=None):
    """Call the xAI Responses API. Returns (answer_text, error_message).

    Exactly one of the two is non-empty.
    """
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        return "", (
            "XAI_API_KEY is not set. Create a key at https://console.x.ai "
            "(API Keys page), add prepaid credits under Billing, then set the "
            "XAI_API_KEY environment variable before starting this server."
        )
    if not prompt or not prompt.strip():
        return "", "prompt must not be empty."
    model = (model or DEFAULT_MODEL).strip()
    system_text = (system or DEFAULT_SYSTEM).strip()

    body = {
        "model": model,
        "input": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": prompt},
        ],
        "store": False,
    }
    request = urllib.request.Request(
        XAI_API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:500]
        except Exception:
            pass
        return "", "xAI API returned HTTP %s. %s" % (e.code, detail)
    except urllib.error.URLError as e:
        return "", "Could not reach the xAI API: %s" % (e.reason,)
    except TimeoutError:
        return "", "The xAI API did not answer within %s seconds." % HTTP_TIMEOUT_SECONDS
    except (ValueError, KeyError) as e:
        return "", "Could not read the xAI API response: %s" % (e,)

    text = extract_text(payload)
    if not text:
        return "", "The xAI API answered but returned no text."
    return text, ""


# --------------------------------------------------------------------------
# MCP (JSON-RPC 2.0 over stdio)
# --------------------------------------------------------------------------

ASK_GROK_TOOL = {
    "name": "ask_grok",
    "description": (
        "Ask Grok (xAI) a question and get its answer back as text. "
        "Use it when you need Grok's knowledge: current information, a second "
        "opinion on a design or a piece of code, or something you do not know. "
        "Send one complete question per call; the answer comes back as a single "
        "text reply. Requires the server's XAI_API_KEY to be set."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The complete question or task for Grok.",
            },
            "model": {
                "type": "string",
                "description": "Grok model to use, e.g. grok-4.7 (default) or grok-4.3.",
                "default": DEFAULT_MODEL,
            },
            "system": {
                "type": "string",
                "description": "Optional system prompt. Defaults to a short instruction to answer directly.",
            },
        },
        "required": ["prompt"],
        "additionalProperties": False,
    },
}


def handle_initialize(params):
    protocol = PROTOCOL_VERSION
    if isinstance(params, dict) and isinstance(params.get("protocolVersion"), str):
        protocol = params["protocolVersion"]
    return {
        "protocolVersion": protocol,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    }


def handle_tools_list(_params):
    return {"tools": [ASK_GROK_TOOL]}


def handle_tools_call(params):
    if not isinstance(params, dict):
        return {"content": [{"type": "text", "text": "Missing tool call parameters."}],
                "isError": True}
    if params.get("name") != "ask_grok":
        return {"content": [{"type": "text",
                             "text": "Unknown tool: %s" % (params.get("name"),)}],
                "isError": True}
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        return {"content": [{"type": "text", "text": "Tool arguments must be an object."}],
                "isError": True}
    answer, error = ask_grok(
        args.get("prompt", ""),
        model=args.get("model") or DEFAULT_MODEL,
        system=args.get("system"),
    )
    if error:
        return {"content": [{"type": "text", "text": error}], "isError": True}
    return {"content": [{"type": "text", "text": answer}], "isError": False}


def handle_message(message):
    """Handle one JSON-RPC message. Returns a response dict, or None."""
    if not isinstance(message, dict):
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "Invalid Request"}}
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}

    # Notifications (no id) need no reply.
    if msg_id is None:
        return None

    try:
        if method == "initialize":
            result = handle_initialize(params)
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = handle_tools_list(params)
        elif method == "tools/call":
            result = handle_tools_call(params)
        else:
            return {"jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": -32601, "message": "Method not found: %s" % (method,)}}
    except Exception as e:  # keep the server alive for the client
        log("error handling %s: %s" % (method, e))
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32603, "message": "Internal error: %s" % (e,)}}
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }) + "\n")
            sys.stdout.flush()
            continue
        response = handle_message(message)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
