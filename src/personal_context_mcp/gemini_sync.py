"""Push the Personal Context digest into one Google Doc for Gemini.

Gemini plans without custom apps cannot reach the MCP server. A Gemini Gem
can, however, use a Google Doc as knowledge. This module keeps one Doc up to
date with the digest (``digest.py``): created the first time, replaced in
place afterwards, so the Doc id - and the Gem that points at it - never
changes.

Authorization is a standard installed-app OAuth flow (loopback redirect,
PKCE) with a single scope, ``drive.file``: the app can see and change only
the files it created, nothing else in the Drive.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import httpx

from personal_context_mcp.config import Settings

SCOPE = "https://www.googleapis.com/auth/drive.file"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - an endpoint, not a secret
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
DOC_MIME = "application/vnd.google-apps.document"
STATE_FILE = Path(".data/gemini-sync.json")


class DriveSyncError(RuntimeError):
    pass


def doc_url(doc_id: str) -> str:
    return f"https://docs.google.com/document/d/{doc_id}/edit"


# --------------------------------------------------------------------------
# One-time authorization
# --------------------------------------------------------------------------


def authorize(settings: Settings, *, open_browser: bool = True, timeout: float = 300) -> str:
    """Run the browser sign-in once and return the refresh token."""
    if (
        not settings.google_drive_client_id
        or not settings.google_drive_client_secret.get_secret_value()
    ):
        raise DriveSyncError(
            "Set PC_GOOGLE_DRIVE_CLIENT_ID and PC_GOOGLE_DRIVE_CLIENT_SECRET first "
            "(docs/guides/11-gemini-without-custom-apps.md)."
        )
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    state = secrets.token_urlsafe(16)
    received: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            received.update(query)
            ok = query.get("state") == state and "code" in query
            self.send_response(200 if ok else 400)
            self.send_header("content-type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"Done. You can close this tab and return to the terminal."
                if ok
                else b"Sign-in did not complete. Return to the terminal."
            )

        def log_message(self, *args: Any) -> None:
            return None

    server = HTTPServer(("127.0.0.1", 0), Handler)
    redirect_uri = f"http://127.0.0.1:{server.server_address[1]}"
    url = (
        AUTH_URL
        + "?"
        + urllib.parse.urlencode(
            {
                "client_id": settings.google_drive_client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "code_challenge": challenge.decode(),
                "code_challenge_method": "S256",
                "state": state,
            }
        )
    )
    thread = threading.Thread(target=server.handle_request, daemon=True)
    server.timeout = timeout
    thread.start()
    print(f"Opening Google sign-in. If no browser opens, visit:\n{url}\n")
    if open_browser:
        webbrowser.open(url)
    thread.join(timeout + 5)
    server.server_close()

    if received.get("state") != state or "code" not in received:
        raise DriveSyncError(f"Sign-in did not complete ({received.get('error', 'no response')}).")
    response = httpx.post(
        TOKEN_URL,
        data={
            "code": received["code"],
            "client_id": settings.google_drive_client_id,
            "client_secret": settings.google_drive_client_secret.get_secret_value(),
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        },
        timeout=30,
    )
    if response.status_code != 200 or "refresh_token" not in response.json():
        raise DriveSyncError(f"Google refused the sign-in (HTTP {response.status_code}).")
    return str(response.json()["refresh_token"])


# --------------------------------------------------------------------------
# Weekly push
# --------------------------------------------------------------------------


def _remember(state_file: Path, doc_id: str) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"doc_id": doc_id}))


def _known_doc_id(settings: Settings, state_file: Path) -> str | None:
    if settings.gemini_digest_doc_id:
        return settings.gemini_digest_doc_id
    try:
        return json.loads(state_file.read_text()).get("doc_id") or None
    except (FileNotFoundError, ValueError):
        return None


async def _access_token(client: httpx.AsyncClient, settings: Settings) -> str:
    refresh = settings.google_drive_refresh_token.get_secret_value()
    if not refresh:
        raise DriveSyncError("Not authorized yet. Run: personal-context-mcp gemini-sync authorize")
    response = await client.post(
        TOKEN_URL,
        data={
            "client_id": settings.google_drive_client_id,
            "client_secret": settings.google_drive_client_secret.get_secret_value(),
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        },
    )
    if response.status_code != 200:
        raise DriveSyncError(
            f"Google refused the saved authorization (HTTP {response.status_code}). "
            "Run: personal-context-mcp gemini-sync authorize"
        )
    return str(response.json()["access_token"])


async def push(
    settings: Settings,
    markdown: str,
    *,
    client: httpx.AsyncClient | None = None,
    state_file: Path = STATE_FILE,
) -> tuple[str, bool]:
    """Create or replace the Doc. Returns (doc id, created)."""
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=60)
    try:
        token = await _access_token(client, settings)
        auth = {"Authorization": f"Bearer {token}"}
        doc_id = _known_doc_id(settings, state_file)
        if doc_id:
            response = await client.patch(
                f"{UPLOAD_URL}/{doc_id}",
                params={"uploadType": "media"},
                headers={**auth, "Content-Type": "text/markdown; charset=utf-8"},
                content=markdown.encode("utf-8"),
            )
            if response.status_code == 200:
                return doc_id, False
            if response.status_code != 404:
                raise DriveSyncError(f"Updating the Doc failed (HTTP {response.status_code}).")
            # Deleted in Drive: fall through and create a new one.

        boundary = "pc-" + secrets.token_hex(12)
        metadata = json.dumps({"name": settings.gemini_digest_doc_name, "mimeType": DOC_MIME})
        body = (
            (
                f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{metadata}\r\n"
                f"--{boundary}\r\nContent-Type: text/markdown; charset=UTF-8\r\n\r\n"
            ).encode()
            + markdown.encode("utf-8")
            + f"\r\n--{boundary}--\r\n".encode()
        )
        response = await client.post(
            UPLOAD_URL,
            params={"uploadType": "multipart", "fields": "id"},
            headers={**auth, "Content-Type": f"multipart/related; boundary={boundary}"},
            content=body,
        )
        if response.status_code not in (200, 201):
            raise DriveSyncError(f"Creating the Doc failed (HTTP {response.status_code}).")
        doc_id = str(response.json()["id"])
        await asyncio.to_thread(_remember, state_file, doc_id)
        return doc_id, True
    finally:
        if own_client:
            await client.aclose()
