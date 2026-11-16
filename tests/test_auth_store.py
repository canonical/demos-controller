from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from demo_controller.api import create_app
from demo_controller.auth import Authenticator
from demo_controller.models import DeployRequest
from demo_controller.settings import Settings
from demo_controller.store import DEMO_LIFETIME_SECONDS, Store


def bearer_request(
    authorization: str = "Bearer test-token",
    repository: str = "canonical/example",
) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/deploy",
            "headers": [
                (b"x-demos-repository", repository.encode()),
                (b"authorization", authorization.encode()),
            ],
            "query_string": b"",
        }
    )


async def test_bearer_authentication() -> None:
    auth = Authenticator({"canonical/example": "test-token"}, 60)
    assert await auth.authenticate(bearer_request()) == "canonical/example"
    assert (
        await auth.authenticate(bearer_request("bearer test-token", "Canonical/Example"))
        == "canonical/example"
    )


@pytest.mark.parametrize(
    ("authorization", "repository"),
    [
        ("", "canonical/example"),
        ("Bearer", "canonical/example"),
        ("Basic test-token", "canonical/example"),
        ("Bearer ", "canonical/example"),
        ("Bearer wrong-token", "canonical/example"),
        ("Bearer  test-token", "canonical/example"),
        ("Bearer test-token ", "canonical/example"),
        ("Bearer tést-token", "canonical/example"),
        ("Bearer test-token", ""),
        ("Bearer test-token", "canonical/other"),
    ],
)
async def test_invalid_bearer_credentials(authorization: str, repository: str) -> None:
    auth = Authenticator({"canonical/example": "test-token"}, 60)
    with pytest.raises(HTTPException) as error:
        await auth.authenticate(bearer_request(authorization, repository))
    assert error.value.status_code == 401
    assert error.value.headers == {"WWW-Authenticate": "Bearer"}


async def test_invalid_token_does_not_consume_repository_rate_limit() -> None:
    auth = Authenticator({"canonical/example": "test-token"}, 1)
    with pytest.raises(HTTPException) as error:
        await auth.authenticate(bearer_request("Bearer wrong-token"))
    assert error.value.status_code == 401
    assert await auth.authenticate(bearer_request()) == "canonical/example"
    with pytest.raises(HTTPException) as error:
        await auth.authenticate(bearer_request())
    assert error.value.status_code == 429


@pytest.mark.parametrize(
    "credentials",
    [
        {},
        [],
        {"canonical/example": ""},
        {"Canonical/Example": "token"},
        {"invalid": "token"},
        {"canonical/example": 123},
        {"canonical/example": "token with spaces"},
    ],
)
def test_settings_reject_invalid_token_maps(monkeypatch, credentials) -> None:
    monkeypatch.setenv("API_TOKENS", json.dumps(credentials))
    with pytest.raises(ValueError, match="API_TOKENS"):
        Settings.from_env()


def test_store_is_idempotent_and_contains_no_project_secrets(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    store = Store(path, "demos.canonical.com")
    request = DeployRequest(
        repository="canonical/example",
        pr=8,
        commit_sha="a" * 40,
        image=f"ghcr.io/canonical/example@sha256:{'b' * 64}",
        delivery_id="delivery-8",
    )
    first, created = store.upsert_deploy(request, "same-hash")
    second, changed = store.upsert_deploy(request, "same-hash")
    assert created is True
    assert changed is False
    assert first == second
    reopened = Store(path, "demos.canonical.com")
    assert reopened.upsert_deploy(request, "same-hash") == (first, False)
    assert first.expires_at == first.created_at + DEMO_LIFETIME_SECONDS
    assert b"application-secret-value" not in path.read_bytes()


def test_deploy_update_preserves_the_active_route_until_reconciled(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db", "demos.canonical.com")
    first = DeployRequest(
        repository="canonical/example",
        pr=8,
        commit_sha="a" * 40,
        image=f"ghcr.io/canonical/example@sha256:{'b' * 64}",
        delivery_id="delivery-8-a",
    )
    store.upsert_deploy(first, "first-hash")
    store.update_state(
        first.repository,
        first.pr,
        "ready",
        "deployment is ready",
        vault_version=2,
        port=8000,
    )
    update = first.model_copy(
        update={
            "commit_sha": "c" * 40,
            "image": f"ghcr.io/canonical/example@sha256:{'d' * 64}",
            "delivery_id": "delivery-8-b",
        }
    )

    pending, changed = store.upsert_deploy(update, "second-hash")

    assert changed is True
    assert pending.state == "pending"
    assert pending.port == 8000
    assert pending.vault_version == 2
    assert store.get_ready_by_hostname(store.status(pending).hostname) == pending


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("API_TOKENS", '{"canonical/example":"test-token"}')
    settings = replace(Settings.from_env(), database_path=tmp_path / "state.db")
    store = Store(settings.database_path, settings.hostname_suffix)
    auth = Authenticator(settings.api_tokens, 60)

    class ReconciliationStub:
        async def run(self) -> None:
            return

        def stop(self) -> None:
            return

    app = create_app(settings, store, auth, ReconciliationStub())  # type: ignore[arg-type]
    with TestClient(app) as client:
        yield client


def deploy_payload() -> dict:
    return {
        "repository": "canonical/example",
        "pr": 12,
        "commit_sha": "a" * 40,
        "image": f"ghcr.io/canonical/example@sha256:{'b' * 64}",
        "delivery_id": "delivery-12",
    }


HEADERS = {
    "Authorization": "Bearer test-token",
    "X-Demos-Repository": "canonical/example",
}


def test_deploy_retry_and_conflicting_delivery(api_client) -> None:
    payload = deploy_payload()
    response = api_client.post("/api/v1/deploy", json=payload, headers=HEADERS)
    assert response.status_code == 202
    assert response.json()["state"] == "pending"
    retry = api_client.post("/api/v1/deploy", json=payload, headers=HEADERS)
    assert retry.status_code == 202
    assert retry.json() == response.json()
    payload["commit_sha"] = "c" * 40
    conflict = api_client.post("/api/v1/deploy", json=payload, headers=HEADERS)
    assert conflict.status_code == 409


def test_destroy_retry_and_conflicting_delivery(api_client) -> None:
    payload = deploy_payload()
    assert api_client.post("/api/v1/deploy", json=payload, headers=HEADERS).status_code == 202
    destruction = {
        "repository": payload["repository"],
        "pr": payload["pr"],
        "delivery_id": "destroy-12",
    }
    response = api_client.post("/api/v1/destroy", json=destruction, headers=HEADERS)
    assert response.status_code == 202
    assert response.json()["state"] == "deleting"
    retry = api_client.post("/api/v1/destroy", json=destruction, headers=HEADERS)
    assert retry.status_code == 202
    assert retry.json() == response.json()
    # A delivery ID cannot be reused for a different operation on this demo.
    payload["delivery_id"] = destruction["delivery_id"]
    assert api_client.post("/api/v1/deploy", json=payload, headers=HEADERS).status_code == 409


@pytest.mark.parametrize("operation", ["deploy", "destroy", "status"])
def test_repository_isolation(api_client, operation: str) -> None:
    if operation == "status":
        response = api_client.get("/api/v1/demos/canonical/other/12", headers=HEADERS)
    else:
        payload = deploy_payload()
        payload["repository"] = "canonical/other"
        payload["image"] = f"ghcr.io/canonical/other@sha256:{'b' * 64}"
        if operation == "destroy":
            payload = {key: payload[key] for key in ("repository", "pr", "delivery_id")}
        response = api_client.post(f"/api/v1/{operation}", json=payload, headers=HEADERS)
    assert response.status_code == 403


@pytest.mark.parametrize("path", ["/api/v1/deploy", "/api/v1/destroy"])
def test_missing_token_rejected(api_client, path: str) -> None:
    response = api_client.post(path, json=deploy_payload())
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_status_requires_token(api_client) -> None:
    path = "/api/v1/demos/canonical/example/12"
    assert api_client.get(path).status_code == 401
    assert (
        api_client.post("/api/v1/deploy", json=deploy_payload(), headers=HEADERS).status_code == 202
    )
    assert api_client.get(path, headers=HEADERS).status_code == 200


def test_hmac_headers_are_not_accepted(api_client) -> None:
    response = api_client.post(
        "/api/v1/deploy",
        json=deploy_payload(),
        headers={
            "X-Demos-Repository": "canonical/example",
            "X-Demos-Timestamp": "123",
            "X-Demos-Nonce": "delivery-12",
            "X-Demos-Signature": "sha256=obsolete",
        },
    )
    assert response.status_code == 401


def test_unauthenticated_health_checks(api_client) -> None:
    assert api_client.get("/healthz").status_code == 200
    assert api_client.get("/readyz").status_code == 200
