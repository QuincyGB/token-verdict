"""HTTP + discovery pins for the two new routes:
POST /v1/pre-trade ($0.05) and POST /v1/batch-verdict ($0.20 flat).

Unpaid calls must 402 with well-formed challenges carrying the exact
prices (50000 / 200000 USDC units on Base, canonical asset, fleet
payTo). Discovery surfaces (manifest, Bazaar extensions, /quote) must
agree with the live challenges. Engine behaviour is pinned separately
in test_pretrade_batch.py and test_wallet_legs.py; this file never
reaches a paid handler, so it runs fully offline.
"""

import base64
import json

import jsonschema
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.main as main
from app.config import load_settings
from app.discovery import (
    BATCH_OUTPUT_EXAMPLE,
    BATCH_OUTPUT_SCHEMA,
    PRE_TRADE_OUTPUT_EXAMPLE,
    PRE_TRADE_OUTPUT_SCHEMA,
    bazaar_batch_extension,
    bazaar_pre_trade_extension,
    x402_manifest,
)

PAY_TO = "0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F"
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
TOKEN = "0x2821d96B5FC81aEC3763340b375a846a89A3Ec1b"


@pytest.fixture(scope="module")
def client():
    return TestClient(main.app, raise_server_exceptions=False)


def _challenge(resp) -> dict:
    header = resp.headers.get("payment-required")
    assert header, "402 response is missing the payment-required header"
    return json.loads(base64.b64decode(header))


def _accepts(resp) -> list[dict]:
    return _challenge(resp)["accepts"]


# --- 402 challenges -------------------------------------------------------------

def test_pre_trade_unpaid_402_exact_price(client):
    r = client.post("/v1/pre-trade", json={"token_address": TOKEN})
    assert r.status_code == 402
    accepts = _accepts(r)
    assert len(accepts) == 1  # Base-only route: no Solana accept
    a = accepts[0]
    assert a["amount"] == "50000"
    assert a["asset"] == USDC_BASE
    assert a["network"] == "eip155:8453"
    assert a["payTo"] == PAY_TO
    assert a["scheme"] == "exact"


def test_batch_unpaid_402_exact_price(client):
    r = client.post("/v1/batch-verdict", json={"token_addresses": [TOKEN]})
    assert r.status_code == 402
    accepts = _accepts(r)
    assert len(accepts) == 1
    a = accepts[0]
    assert a["amount"] == "200000"
    assert a["asset"] == USDC_BASE
    assert a["network"] == "eip155:8453"
    assert a["payTo"] == PAY_TO


def test_verdict_price_unchanged(client):
    r = client.post("/v1/verdict", json={"address": TOKEN})
    assert r.status_code == 402
    amounts = {a["amount"] for a in _accepts(r)}
    assert "20000" in amounts


def test_new_routes_registered_with_extensions():
    for path, ext_fn in (
        ("POST /v1/pre-trade", bazaar_pre_trade_extension),
        ("POST /v1/batch-verdict", bazaar_batch_extension),
    ):
        route = main.routes[path]
        assert route.extensions["bazaar"] == ext_fn()
        assert len(route.description) < 500
        assert "Use this when" in route.description


# --- request model bounds ---------------------------------------------------------

def test_batch_model_bounds():
    ok = main.BatchVerdictRequest(token_addresses=[TOKEN] * 25)
    assert len(ok.token_addresses) == 25
    with pytest.raises(ValidationError):
        main.BatchVerdictRequest(token_addresses=[])
    with pytest.raises(ValidationError):
        main.BatchVerdictRequest(token_addresses=[TOKEN] * 26)


def test_pre_trade_model_defaults():
    req = main.PreTradeRequest(token_address=TOKEN)
    assert req.chain == "base"
    assert req.wallet_address is None and req.spender is None


# --- discovery consistency -----------------------------------------------------------

def test_manifest_lists_new_routes_with_exact_amounts():
    settings = load_settings()
    manifest = x402_manifest(settings, "https://example.test")
    by_url = {r["resource"]: r for r in manifest["resources"]}

    pre = by_url["https://example.test/v1/pre-trade"]
    assert pre["price"] == "$0.05"
    assert pre["accepts"][0]["amount"] == "50000"
    assert pre["accepts"][0]["payTo"] == PAY_TO
    assert pre["accepts"][0]["asset"] == USDC_BASE
    assert len(pre["description"]) < 500 and "Use this when" in pre["description"]

    batch = by_url["https://example.test/v1/batch-verdict"]
    assert batch["price"] == "$0.20"
    assert batch["accepts"][0]["amount"] == "200000"
    assert len(batch["description"]) < 500 and "Use this when" in batch["description"]


def test_new_extensions_pass_sdk_validators():
    from x402.extensions.bazaar import (
        validate_discovery_extension,
        validate_discovery_extension_spec,
    )

    for ext in (bazaar_pre_trade_extension(), bazaar_batch_extension()):
        result = validate_discovery_extension(ext)
        assert result.valid, result.errors
        spec = validate_discovery_extension_spec(ext)
        assert spec.valid, spec.errors


def test_new_examples_validate_against_output_schemas():
    jsonschema.validate(instance=PRE_TRADE_OUTPUT_EXAMPLE, schema=PRE_TRADE_OUTPUT_SCHEMA)
    jsonschema.validate(instance=BATCH_OUTPUT_EXAMPLE, schema=BATCH_OUTPUT_SCHEMA)


def test_quote_lists_all_paid_endpoints(client):
    body = client.get("/quote").json()
    endpoints = {e["endpoint"]: e["price"] for e in body["endpoints"]}
    assert endpoints == {
        "/v1/verdict": "$0.02",
        "/v1/pre-trade": "$0.05",
        "/v1/batch-verdict": "$0.20",
    }
    # Pre-existing keys untouched.
    assert body["price"] == "$0.02" and body["payTo"] == PAY_TO


def test_openapi_lists_new_paths(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/v1/pre-trade" in paths
    assert "/v1/batch-verdict" in paths
