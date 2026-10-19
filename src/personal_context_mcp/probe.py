"""Walk the handshake an MCP client performs, against a running server.

``personal-context-mcp probe <url>`` does, over plain HTTP JSON-RPC and with no
MCP SDK in between, what ChatGPT, Claude, Gemini and Grok do when they connect:

Without a token, the discovery chain a client follows before it can sign in:
the 401 challenge, the protected resource metadata it points at, and the
authorization server metadata (Dynamic Client Registration, PKCE S256).

With a token, the session itself: ``initialize`` and protocol negotiation,
``tools/list`` with every schema checked, and a read-only call of
``context_bootstrap`` and ``review_list``.

Every response body is printed as received. The token is never printed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft202012Validator, SchemaError, ValidationError

#: The newest protocol revision the server is expected to accept.
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

#: Spec-recommended tool name shape; some clients reject anything else.
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

#: Read-only calls made once the tool list checks out.
DRY_RUN_CALLS: tuple[tuple[str, dict[str, Any]], ...] = (
    # user_message, not query: the probe must also work against a server that
    # predates the query parameter.
    ("context_bootstrap", {"user_message": "Connection check from personal-context-mcp probe."}),
    ("review_list", {"limit": 1}),
)


@dataclass
class ProbeReport:
    failures: list[str] = field(default_factory=list)
    checks: int = 0

    def check(self, ok: bool, message: str) -> bool:
        self.checks += 1
        if not ok:
            self.failures.append(message)
        return ok


def _decode(response: httpx.Response) -> Any:
    """A JSON-RPC reply, whether the server answered in JSON or one SSE event."""
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:") :])
        return None
    return response.json()


def schema_problems(where: str, schema: Any) -> list[str]:
    """Why a tool's input schema could trip a client. Empty when it is clean."""
    if not isinstance(schema, dict):
        return [f"{where}: schema is not an object"]
    problems = []
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        problems.append(f"{where}: invalid JSON Schema: {exc.message}")
    if schema.get("type") != "object":
        problems.append(f"{where}: top-level type is {schema.get('type')!r}, not 'object'")
    properties = schema.get("properties", {})
    for name in schema.get("required", []):
        if name not in properties:
            problems.append(f"{where}: required {name!r} is not a declared property")
    for name, prop in properties.items():
        if not isinstance(prop, dict):
            problems.append(f"{where}.{name}: property schema is not an object")
            continue
        if not prop.get("description"):
            problems.append(f"{where}.{name}: no description")
        branches = prop.get("anyOf") or prop.get("oneOf") or [prop]
        if not all(
            isinstance(branch, dict) and ("type" in branch or "enum" in branch)
            for branch in branches
        ):
            problems.append(f"{where}.{name}: no explicit type")
    if "$ref" in json.dumps(schema):
        problems.append(f"{where}: uses $ref, which several clients do not resolve")
    return problems


def run_probe(
    url: str,
    *,
    token: str | None,
    protocol_version: str = DEFAULT_PROTOCOL_VERSION,
    oauth: bool = True,
    echo: Callable[[str], None] = print,
    client: httpx.Client | None = None,
) -> ProbeReport:
    report = ProbeReport()
    http = client or httpx.Client(timeout=30.0, follow_redirects=False)
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
        "user-agent": "personal-context-mcp-probe",
    }

    def show(label: str, response: httpx.Response) -> Any:
        echo(f"\n--- {label}: HTTP {response.status_code}")
        try:
            body = _decode(response)
        except ValueError:
            echo(response.text[:2000])
            return None
        echo(json.dumps(body, indent=2))
        return body

    def rpc(method: str, params: dict[str, Any] | None, rpc_id: int | None) -> httpx.Response:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if rpc_id is not None:
            message["id"] = rpc_id
        auth = {"authorization": f"Bearer {token}"} if token else {}
        return http.post(
            url,
            json=message,
            headers={**headers, **auth, "mcp-protocol-version": protocol_version},
        )

    try:
        if oauth:
            _discovery(url, http, headers, report, show)
        if token:
            _session(url, rpc, report, show, protocol_version, echo)
        else:
            echo("\nNo token given: skipped initialize, tools/list and the tool calls.")
    finally:
        if client is None:
            http.close()
    return report


def _discovery(
    url: str,
    http: httpx.Client,
    headers: dict[str, str],
    report: ProbeReport,
    show: Callable[[str, httpx.Response], Any],
) -> None:
    """The unauthenticated path a client walks before it can sign in."""
    initialize = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": DEFAULT_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "personal-context-mcp-probe", "version": "1"},
        },
    }
    response = http.post(url, json=initialize, headers=headers)
    show("unauthenticated initialize", response)
    if response.status_code != 401:
        report.check(
            response.status_code == 200,
            f"unauthenticated initialize returned HTTP {response.status_code}",
        )
        return
    challenge = response.headers.get("www-authenticate", "")
    metadata_url = None
    match = re.search(r'resource_metadata="([^"]+)"', challenge)
    if report.check(
        match is not None,
        f"401 challenge has no resource_metadata, so clients must guess: {challenge!r}",
    ):
        assert match is not None
        metadata_url = match.group(1)
    else:
        parts = urlsplit(url)
        metadata_url = (
            f"{parts.scheme}://{parts.netloc}/.well-known/oauth-protected-resource{parts.path}"
        )

    response = http.get(metadata_url)
    resource = show("protected resource metadata", response)
    if not report.check(
        response.status_code == 200 and isinstance(resource, dict),
        f"protected resource metadata at {metadata_url} returned HTTP {response.status_code}",
    ):
        return
    report.check(
        resource.get("resource", "").rstrip("/") == url.rstrip("/"),
        f"metadata names resource {resource.get('resource')!r}, but the URL probed is {url!r}",
    )
    servers = resource.get("authorization_servers") or []
    if not report.check(bool(servers), "metadata lists no authorization_servers"):
        return

    issuer = str(servers[0]).rstrip("/")
    parts = urlsplit(issuer)
    as_url = f"{parts.scheme}://{parts.netloc}/.well-known/oauth-authorization-server{parts.path}"
    response = http.get(as_url)
    server = show("authorization server metadata", response)
    if not report.check(
        response.status_code == 200 and isinstance(server, dict),
        f"authorization server metadata at {as_url} returned HTTP {response.status_code}",
    ):
        return
    report.check(
        bool(server.get("registration_endpoint")),
        "no registration_endpoint: clients cannot register themselves (DCR)",
    )
    report.check(
        "S256" in (server.get("code_challenge_methods_supported") or []),
        "PKCE S256 is not advertised",
    )


def _session(
    url: str,
    rpc: Callable[[str, dict[str, Any] | None, int | None], httpx.Response],
    report: ProbeReport,
    show: Callable[[str, httpx.Response], Any],
    protocol_version: str,
    echo: Callable[[str], None],
) -> None:
    """initialize, tools/list and the read-only calls, with a token."""
    response = rpc(
        "initialize",
        {
            "protocolVersion": protocol_version,
            "capabilities": {},
            "clientInfo": {"name": "personal-context-mcp-probe", "version": "1"},
        },
        1,
    )
    body = show("initialize", response)
    result = body.get("result") if isinstance(body, dict) else None
    if not report.check(
        response.status_code == 200 and isinstance(result, dict),
        f"initialize failed with HTTP {response.status_code}",
    ):
        return
    assert isinstance(result, dict)
    report.check(
        result.get("protocolVersion") == protocol_version,
        f"asked for protocol {protocol_version}, server chose {result.get('protocolVersion')}",
    )
    report.check(
        "tools" in (result.get("capabilities") or {}),
        "initialize did not advertise the tools capability",
    )

    response = rpc("notifications/initialized", None, None)
    show("notifications/initialized", response)
    report.check(
        response.status_code in (200, 202),
        f"notifications/initialized returned HTTP {response.status_code}",
    )

    tools: list[dict[str, Any]] = []
    cursor: str | None = None
    page = 0
    while True:
        page += 1
        response = rpc("tools/list", {"cursor": cursor} if cursor else {}, 1 + page)
        body = show(f"tools/list page {page}", response)
        result = body.get("result") if isinstance(body, dict) else None
        if not report.check(
            response.status_code == 200 and isinstance(result, dict),
            f"tools/list page {page} failed with HTTP {response.status_code}: {response.text[:300]}",
        ):
            return
        assert isinstance(result, dict)
        tools.extend(result.get("tools", []))
        cursor = result.get("nextCursor")
        if not cursor:
            break
    echo(f"\n--- tools/list: {len(tools)} tools over {page} page(s)")
    echo(json.dumps([tool.get("name") for tool in tools]))

    problems: list[str] = []
    by_name = {}
    for tool in tools:
        name = tool.get("name", "")
        by_name[name] = tool
        if not _TOOL_NAME.match(name):
            problems.append(f"{name!r}: tool name has characters some clients reject")
        if not tool.get("description"):
            problems.append(f"{name}: no description")
        problems.extend(schema_problems(name, tool.get("inputSchema")))
        if "outputSchema" in tool:
            try:
                Draft202012Validator.check_schema(tool["outputSchema"])
            except SchemaError as exc:
                problems.append(f"{name}: invalid outputSchema: {exc.message}")
    for problem in problems:
        report.check(False, f"schema: {problem}")
    report.check(bool(tools), "tools/list returned no tools: check the token's scopes")
    echo(f"schema validation failures: {len(problems)}")

    for offset, (name, arguments) in enumerate(DRY_RUN_CALLS):
        if not report.check(
            name in by_name, f"{name} is not in tools/list (missing scope for this client?)"
        ):
            continue
        response = rpc("tools/call", {"name": name, "arguments": arguments}, 100 + offset)
        body = show(f"tools/call {name}", response)
        result = body.get("result") if isinstance(body, dict) else None
        if not report.check(
            isinstance(result, dict), f"{name}: no JSON-RPC result (HTTP {response.status_code})"
        ):
            continue
        assert isinstance(result, dict)
        report.check(not result.get("isError"), f"{name}: the tool reported an error")
        output_schema = by_name[name].get("outputSchema")
        if output_schema and "structuredContent" in result:
            try:
                Draft202012Validator(output_schema).validate(result["structuredContent"])
            except ValidationError as exc:
                report.check(False, f"{name}: result does not match outputSchema: {exc.message}")
