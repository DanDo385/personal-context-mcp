"""The importable app explains a missing configuration instead of crashing."""

from __future__ import annotations

from starlette.testclient import TestClient

from personal_context_mcp.asgi import NOT_CONFIGURED, not_configured_app


def test_unconfigured_deployment_answers_503_and_names_no_setting(capsys) -> None:
    app = not_configured_app(["PC_DATABASE_URL is required."])
    client = TestClient(app)

    for method, path in [("GET", "/healthz"), ("POST", "/mcp"), ("GET", "/")]:
        response = client.request(method, path)
        assert response.status_code == 503
        assert response.text == NOT_CONFIGURED
        assert "PC_DATABASE_URL" not in response.text

    # The specifics go to the logs, where only the person deploying sees them.
    assert "PC_DATABASE_URL is required." in capsys.readouterr().err
