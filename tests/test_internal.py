"""Fleet-internal route gate: /v1/internal/verdict serves only the
fleet (StallRow's orchestrate), only with the shared secret, and
does not exist when the secret is unset."""

import pytest
from fastapi.testclient import TestClient

import app.main as main

BODY = {"address": "0x940181a94A35A4569E4529A3CDfB74e38FD98631", "chain": "base"}


@pytest.fixture()
def client():
    return TestClient(main.app, raise_server_exceptions=False)


def _set_secret(value):
    object.__setattr__(main.settings, "fleet_internal_secret", value)


def test_internal_route_404s_when_secret_unset(client):
    _set_secret(None)
    r = client.post("/v1/internal/verdict", json=BODY)
    assert r.status_code == 404


def test_internal_route_403s_on_wrong_secret(client):
    _set_secret("test-secret")
    r = client.post(
        "/v1/internal/verdict", json=BODY, headers={"x-fleet-internal-secret": "nope"}
    )
    assert r.status_code == 403


def test_internal_route_serves_with_secret(client, monkeypatch):
    _set_secret("test-secret")

    async def stub(req):
        return {"verdict": "SOLID", "stubbed": True}

    monkeypatch.setattr(main, "_run_verdict", stub)
    r = client.post(
        "/v1/internal/verdict", json=BODY, headers={"x-fleet-internal-secret": "test-secret"}
    )
    assert r.status_code == 200
    assert r.json()["stubbed"] is True


def test_internal_route_absent_from_manifest(client):
    _set_secret("test-secret")
    body = client.get("/.well-known/x402").json()
    urls = [r["resource"] for r in body["resources"]]
    assert not any("internal" in u for u in urls)
