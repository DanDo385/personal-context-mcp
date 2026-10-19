"""Integration tests for the browser extension ingest endpoint.

These tests run against a real database (PC_TEST_DATABASE_URL).
They verify idempotent upsert, message dedup, and device token auth.
"""

from __future__ import annotations

import json
import os

import pytest
from starlette.testclient import TestClient

from personal_context_mcp.config import Settings
from personal_context_mcp.server import create_app

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def client():
    """Create a test client with a fresh app instance."""
    settings = Settings(
        PC_ENVIRONMENT="dev",
        PC_AUTH_MODE="none",
        PC_CLASSIFIER="rules",
        PC_EMBEDDING_PROVIDER="none",
        PC_STORAGE_BACKEND="local",
        PC_LOCAL_STORAGE_ROOT="/tmp/pc-test-storage",
    )
    app = create_app(settings)
    return TestClient(app)


@pytest.fixture(scope="module")
def device_token(client):
    """Register a device and return its token."""
    resp = client.post(
        "/api/v1/auth/register-device",
        json={"device_name": "test-browser"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "device_token" in data
    return data["device_token"]


FIXTURE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "extension", "tests", "fixtures"
)


def _load_fixture(provider: str) -> dict:
    path = os.path.join(FIXTURE_DIR, f"{provider}-conversation.json")
    with open(path) as f:
        return json.load(f)


@pytest.mark.parametrize("test_provider", ["claude", "chatgpt", "gemini", "grok"])
def test_register_device(client, test_provider):
    """Can register a device and receive a token."""
    resp = client.post(
        "/api/v1/auth/register-device",
        json={"device_name": f"test-{test_provider}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["device_token"].startswith("pcd_")
    assert data["device_id"].startswith("dev_")


def test_register_device_no_body(client):
    """Registration with no body still works (defaults device name)."""
    resp = client.post("/api/v1/auth/register-device")
    assert resp.status_code == 200


def test_unauthorized_ingest(client):
    """Ingest without a token returns 401."""
    resp = client.post(
        "/api/v1/ingest/conversation",
        json={"provider": "claude", "provider_conversation_id": "x"},
    )
    assert resp.status_code == 401


def test_unauthorized_handoff(client):
    """Handoff without a token returns 401."""
    resp = client.get("/api/v1/handoff")
    assert resp.status_code == 401


def test_ingest_missing_fields(client, device_token):
    """Ingest without required fields returns 400."""
    resp = client.post(
        "/api/v1/ingest/conversation",
        json={"messages": []},
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert resp.status_code == 400


@pytest.mark.parametrize("provider", ["claude", "chatgpt", "gemini", "grok"])
def test_ingest_new_conversation(client, device_token, provider):
    """Ingest a new conversation creates it."""
    payload = _load_fixture(provider)
    resp = client.post(
        "/api/v1/ingest/conversation",
        json=payload,
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "created"
    assert data["messages_inserted"] == len(payload["messages"])
    assert data["messages_skipped"] == 0


@pytest.mark.parametrize("provider", ["claude", "chatgpt", "gemini", "grok"])
def test_ingest_idempotent(client, device_token, provider):
    """Re-sending the same conversation is idempotent."""
    payload = _load_fixture(provider)
    # First send.
    resp1 = client.post(
        "/api/v1/ingest/conversation",
        json=payload,
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert resp1.status_code == 200
    data1 = resp1.json()

    # Second send — same payload.
    resp2 = client.post(
        "/api/v1/ingest/conversation",
        json=payload,
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert resp2.status_code == 200
    data2 = resp2.json()

    # Same conversation ID.
    assert data2["conversation_id"] == data1["conversation_id"]
    # Status is "updated" (upsert hit the existing row).
    assert data2["status"] == "updated"
    # No new messages inserted (all skipped by dedup).
    assert data2["messages_inserted"] == 0
    assert data2["messages_skipped"] == len(payload["messages"])


@pytest.mark.parametrize("provider", ["claude", "chatgpt"])
def test_ingest_append_messages(client, device_token, provider):
    """Sending a conversation with additional messages appends only the new ones."""
    payload = _load_fixture(provider)
    # First send with original messages.
    resp1 = client.post(
        "/api/v1/ingest/conversation",
        json=payload,
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert resp1.status_code == 200

    # Add a new message.
    payload["messages"].append({
        "provider_message_id": f"{provider}-new-msg-001",
        "role": "assistant",
        "content": "This is a follow-up response that was added later.",
        "created_at": "2026-10-15T12:00:00Z",
        "model": payload.get("model"),
    })

    resp2 = client.post(
        "/api/v1/ingest/conversation",
        json=payload,
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["status"] == "updated"
    assert data["messages_inserted"] == 1  # Only the new message.
    assert data["messages_skipped"] == len(payload["messages"]) - 1


def test_revoke_device(client, device_token):
    """Revoking a device token invalidates it."""
    # First, register to get the device_id.
    resp = client.post(
        "/api/v1/auth/register-device",
        json={"device_name": "revoke-test"},
    )
    data = resp.json()
    device_token_new = data["device_token"]
    device_id = data["device_id"]

    # Revoke it.
    resp = client.delete(
        f"/api/v1/auth/devices/{device_id}",
        headers={"Authorization": f"Bearer {device_token_new}"},
    )
    assert resp.status_code == 200

    # Token should now be invalid.
    resp = client.get(
        "/api/v1/handoff",
        headers={"Authorization": f"Bearer {device_token_new}"},
    )
    assert resp.status_code == 401


def test_handoff_returns_data(client, device_token):
    """Handoff endpoint returns JSON even when empty."""
    resp = client.get(
        "/api/v1/handoff",
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "handoff" in data