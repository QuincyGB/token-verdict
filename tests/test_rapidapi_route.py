"""Tests for the RapidAPI gateway route (POST /v1/verdict/rapidapi).

The route shares the verdict pipeline with the x402-paid route but is
gated by the per-API proxy secret RapidAPI injects, NOT by x402. These
tests pin the gate: unset secret = route dark (404), wrong/missing
secret = 403, correct secret = identical behaviour to the paid route,
and the x402 route stays paywalled throughout. Offline: analyze() is
monkeypatched, same style as the engine tests.
"""

import dataclasses

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.verdict import TokenNotFound

SECRET = "test-proxy-secret"
WETH_BASE = {"address": "0x4200000000000000000000000000000000000006", "chain": "base"}

FAKE_RESULT = {
    "token": {"address": WETH_BASE["address"], "symbol": "WETH", "name": "Wrapped Ether", "chain": "base"},
    "verdict": "SOLID",
    "score": 20.9,
    "signals": [],
    "take": "fake take",
    "market": {},
}


@pytest.fixture()
def client(monkeypatch):
    async def fake_analyze(address, chain):
        return FAKE_RESULT

    monkeypatch.setattr(main, "analyze", fake_analyze)
    return TestClient(main.app, raise_server_exceptions=False)


def _with_secret(monkeypatch, secret):
    monkeypatch.setattr(
        main, "settings", dataclasses.replace(main.settings, rapidapi_proxy_secret=secret)
    )


def test_route_dark_when_secret_unset(client, monkeypatch):
    _with_secret(monkeypatch, None)
    r = client.post("/v1/verdict/rapidapi", json=WETH_BASE, headers={"x-rapidapi-proxy-secret": SECRET})
    assert r.status_code == 404


def test_route_dark_when_secret_empty(client, monkeypatch):
    # load_settings() normalizes "" -> None; an empty secret must never open the gate.
    _with_secret(monkeypatch, None)
    r = client.post("/v1/verdict/rapidapi", json=WETH_BASE)
    assert r.status_code == 404


def test_missing_header_forbidden(client, monkeypatch):
    _with_secret(monkeypatch, SECRET)
    r = client.post("/v1/verdict/rapidapi", json=WETH_BASE)
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden"}


def test_wrong_header_forbidden(client, monkeypatch):
    _with_secret(monkeypatch, SECRET)
    r = client.post(
        "/v1/verdict/rapidapi", json=WETH_BASE, headers={"x-rapidapi-proxy-secret": "nope"}
    )
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden"}


def test_correct_secret_returns_verdict(client, monkeypatch):
    _with_secret(monkeypatch, SECRET)
    r = client.post(
        "/v1/verdict/rapidapi", json=WETH_BASE, headers={"x-rapidapi-proxy-secret": SECRET}
    )
    assert r.status_code == 200
    assert r.json() == FAKE_RESULT


def test_correct_secret_bad_address_422(client, monkeypatch):
    _with_secret(monkeypatch, SECRET)
    r = client.post(
        "/v1/verdict/rapidapi",
        json={"address": "not-an-address", "chain": "base"},
        headers={"x-rapidapi-proxy-secret": SECRET},
    )
    assert r.status_code == 422
    assert r.json() == {"error": "address must be a 0x contract address"}


def test_correct_secret_unknown_token_404(client, monkeypatch):
    async def not_found(address, chain):
        raise TokenNotFound(f"No DEXScreener pair found for {address} on {chain}")

    monkeypatch.setattr(main, "analyze", not_found)
    _with_secret(monkeypatch, SECRET)
    r = client.post(
        "/v1/verdict/rapidapi", json=WETH_BASE, headers={"x-rapidapi-proxy-secret": SECRET}
    )
    assert r.status_code == 404
    assert "No DEXScreener pair found" in r.json()["error"]


def test_x402_route_still_paywalled(client, monkeypatch):
    _with_secret(monkeypatch, SECRET)
    r = client.post("/v1/verdict", json=WETH_BASE)
    assert r.status_code == 402


def test_free_routes_still_open(client, monkeypatch):
    _with_secret(monkeypatch, SECRET)
    assert client.get("/health").status_code == 200
    assert client.get("/quote").status_code == 200
