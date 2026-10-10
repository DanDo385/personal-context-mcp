#!/usr/bin/env python3
"""Gemini handshake test harness for personal-context-mcp.

Executes the complete Custom Apps handshake sequence against a running
server, over raw HTTP, exactly as a third-party MCP client would:

  1. ``initialize`` — session setup and protocol version negotiation.
  2. ``tools/list`` — fetch the manifest and validate schema completeness
     (every property carries an explicit type and description).
  3. Dry-run ``tools/call`` for ``context_bootstrap`` and ``review_list``
     (both read-only, safe against production data).
  4. Negative probe: a call with missing required params must come back as
     a standard JSON-RPC 2.0 error object, never an HTTP 500 or a dropped
     connection.

Prints the raw response payloads and exits nonzero on any schema validation
failure or unexpected transport behavior.

Usage:
    python scripts/gemini_handshake.py --base-url https://context.example.com
    python scripts/gemini_handshake.py --base-url http://127.0.0.1:8080 --token dev-token-change-me

Only the standard library is used, so this runs anywhere Python 3.10+ exists.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2025-06-18"
MCP_PATH = "/mcp"
TIMEOUT = 30


class HandshakeError(Exception):
    """A handshake step failed."""


def _post(url: str, payload: dict | list, token: str | None) -> tuple[int, str, object]:
    """POST one JSON-RPC message. Returns (http_status, content_type, parsed_body)."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            # StreamableHTTP: the client must accept both JSON and SSE.
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "User-Agent": "personal-context-mcp-gemini-handshake/1.0",
        },
        method="POST",
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read().decode("utf-8", "replace")
            return response.status, response.headers.get("Content-Type", ""), _parse(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, exc.headers.get("Content-Type", ""), _parse(raw)
        except HandshakeError:
            raise HandshakeError(f"HTTP {exc.code} with unparsable body: {raw[:500]}") from exc


def _parse(raw: str) -> object:
    """Accept a plain JSON response or a StreamableHTTP SSE stream."""
    text = raw.strip()
    if not text:
        raise HandshakeError("empty response body (dropped connection?)")
    if text.startswith("{") or text.startswith("["):
        return json.loads(text)
    messages: list[object] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            data = line[5:].strip()
            if data and data != "[DONE]":
                messages.append(json.loads(data))
    if not messages:
        raise HandshakeError(f"no JSON-RPC message in response: {raw[:500]}")
    return messages[0] if len(messages) == 1 else messages


def _rpc(url: str, token: str | None, method: str, params: dict | None, rpc_id: int) -> dict:
    status, content_type, payload = _post(
        url,
        {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": method,
            **({"params": params} if params else {}),
        },
        token,
    )
    print(f"--- {method} -> HTTP {status} ({content_type})")
    print(json.dumps(payload, indent=2)[:4000])
    if status == 500:
        raise HandshakeError(f"{method}: HTTP 500 — tool errors must be JSON-RPC errors, not 500s")
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
        raise HandshakeError(f"{method}: not a JSON-RPC 2.0 response envelope")
    if status == 401:
        raise HandshakeError(f"{method}: HTTP 401 — check the token and PC_ALLOWED_SUBJECTS")
    if "error" in payload:
        raise HandshakeError(f"{method}: unexpected JSON-RPC error: {payload['error']}")
    return payload  # type: ignore[return-value]


def _validate_tool(tool: dict) -> list[str]:
    """Every schema property needs an explicit type and a description."""
    failures: list[str] = []
    name = tool.get("name", "<unnamed>")
    if not tool.get("description"):
        failures.append(f"{name}: tool has no description")
    schema = tool.get("inputSchema") or {}
    if schema.get("type") != "object":
        failures.append(f"{name}: inputSchema is not an object schema")
        return failures
    properties = schema.get("properties") or {}
    if not properties:
        return failures  # zero-argument tool: nothing to describe
    for prop_name, prop in properties.items():
        if not isinstance(prop, dict):
            failures.append(f"{name}.{prop_name}: schema is not an object")
            continue
        if "$ref" in prop:
            continue  # resolved against $defs; nothing to check inline
        has_type = bool(
            prop.get("type")
            or prop.get("anyOf")
            or prop.get("oneOf")
            or prop.get("enum")
            or prop.get("const")
        )
        if not has_type:
            failures.append(f"{name}.{prop_name}: no explicit type")
        described = bool(prop.get("description")) or any(
            isinstance(option, dict) and option.get("description")
            for option in prop.get("anyOf", []) + prop.get("oneOf", [])
        )
        if not described:
            failures.append(f"{name}.{prop_name}: no description")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="e.g. https://context.example.com")
    parser.add_argument("--token", default=None, help="Bearer token (omit for auth_mode=none)")
    parser.add_argument("--mcp-path", default=MCP_PATH)
    args = parser.parse_args()

    url = args.base_url.rstrip("/") + args.mcp_path
    rpc_id = 1
    failures: list[str] = []

    print(f"=== 1. initialize {url} (protocol {PROTOCOL_VERSION})")
    try:
        init = _rpc(
            url,
            args.token,
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"roots": {}, "sampling": {}},
                "clientInfo": {"name": "gemini-handshake-harness", "version": "1.0"},
            },
            rpc_id,
        )
        rpc_id += 1
        negotiated = init.get("result", {}).get("protocolVersion")
        print(f"negotiated protocol version: {negotiated}")
    except HandshakeError as exc:
        print(f"FATAL: {exc}")
        return 1

    # Session-establishment notification; fire-and-forget (204/202 expected).
    try:
        _post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, args.token)
    except HandshakeError as exc:
        print(f"note: initialized notification not accepted: {exc}")

    print("\n=== 2. tools/list + schema validation")
    try:
        listed = _rpc(url, args.token, "tools/list", None, rpc_id)
        rpc_id += 1
    except HandshakeError as exc:
        print(f"FATAL: {exc}")
        return 1
    tools = listed.get("result", {}).get("tools", [])
    print(f"tool count: {len(tools)}")
    names = {t.get("name") for t in tools}
    for required in ("context_bootstrap", "note_write", "review_list", "list_get"):
        status = "present" if required in names else "MISSING"
        print(f"  {required}: {status}")
        if required not in names:
            failures.append(f"required tool {required} missing from manifest")
    for tool in tools:
        failures.extend(_validate_tool(tool))

    print("\n=== 3a. dry-run tools/call context_bootstrap")
    try:
        _rpc(
            url,
            args.token,
            "tools/call",
            {
                "name": "context_bootstrap",
                "arguments": {"user_message": "handshake dry run: what is this server?"},
            },
            rpc_id,
        )
        rpc_id += 1
    except HandshakeError as exc:
        failures.append(f"context_bootstrap dry run failed: {exc}")

    print("\n=== 3b. dry-run tools/call review_list")
    try:
        _rpc(url, args.token, "tools/call", {"name": "review_list", "arguments": {}}, rpc_id)
        rpc_id += 1
    except HandshakeError as exc:
        failures.append(f"review_list dry run failed: {exc}")

    print("\n=== 4. negative probe: tools/call with missing required params")
    status, _, payload = _post(
        url,
        {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "tools/call",
            "params": {"name": "context_bootstrap", "arguments": {}},
        },
        args.token,
    )
    print(f"--- tools/call (invalid) -> HTTP {status}")
    print(json.dumps(payload, indent=2)[:2000])
    # Both MCP error channels are acceptable: a JSON-RPC "error" object
    # (protocol-level failures) or a result with "isError: true" (tool-level
    # failures, the channel FastMCP uses for argument validation). What must
    # never happen is an HTTP 500, an HTML body, or a dropped connection.
    if status == 500:
        failures.append("invalid call returned HTTP 500 instead of a structured error")
    elif not isinstance(payload, dict):
        failures.append("invalid call returned no JSON-RPC envelope")
    elif "error" in payload:
        print(f"JSON-RPC error code: {payload['error'].get('code')}")
    elif isinstance(payload.get("result"), dict) and payload["result"].get("isError") is True:
        print("tool-level error channel (isError: true) — correct MCP behavior")
    else:
        failures.append("invalid call returned success for invalid arguments")

    print("\n=== result ===")
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        f"zero schema validation failures across {len(tools)} tools; error path is JSON-RPC clean."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
