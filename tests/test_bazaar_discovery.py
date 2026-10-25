"""Tests for the Bazaar/PayAI cataloging wiring.

Pins the three things the catalog indexes actually check:
  * facilitator selection is config-driven (PayAI by URL alone, CDP only
    with URL + both keys, keyless default otherwise);
  * every description that reaches a 402 / manifest stays UNDER 500
    characters (longer descriptions break CDP payments);
  * the bazaar discovery extension carries a FULL typed output schema
    and passes the x402 SDK's own facilitator-side validators, with the
    shipped example validated against that schema.
"""

import os
import time

import jsonschema
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.config import Settings, load_settings
from app.discovery import (
    VERDICT_OUTPUT_EXAMPLE,
    VERDICT_OUTPUT_SCHEMA,
    bazaar_extension,
    x402_manifest,
)
from app.verdict import (
    apply_dead_market_guard,
    compute_signals,
    score_signals,
    write_take,
)

DAY_MS = 24 * 3600 * 1000
CDP_URL = "https://api.cdp.coinbase.com/platform/v2/x402"
PAYAI_URL = "https://facilitator.payai.network"


def _settings(**env):
    """load_settings() with a controlled environment."""
    keys = ["X402_FACILITATOR_URL", "CDP_API_KEY_ID", "CDP_API_KEY_SECRET"]
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    os.environ.update(env)
    try:
        return load_settings()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _bare_settings(facilitator_url, **kw):
    return Settings(
        pay_to="0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F",
        price_usdc="$0.02",
        network="eip155:8453",
        facilitator_url=facilitator_url,
        service_name="token-verdict",
        fetch_timeout_s=15,
        port=3000,
        **kw,
    )


# --- facilitator selection ----------------------------------------------------

def test_payai_selected_by_url_alone():
    s = _settings(X402_FACILITATOR_URL=PAYAI_URL)
    assert s.use_payai is True
    assert s.use_cdp is False


def test_default_facilitator_is_neither():
    s = _settings()
    assert s.use_payai is False
    assert s.use_cdp is False


def test_cdp_needs_url_and_both_keys():
    assert _settings(X402_FACILITATOR_URL=CDP_URL).use_cdp is False
    assert _settings(X402_FACILITATOR_URL=CDP_URL, CDP_API_KEY_ID="id").use_cdp is False
    s = _settings(
        X402_FACILITATOR_URL=CDP_URL, CDP_API_KEY_ID="id", CDP_API_KEY_SECRET="secret"
    )
    assert s.use_cdp is True
    assert s.use_payai is False


def test_payai_url_constant_matches_settings_selection():
    from app.config import PAYAI_FACILITATOR_URL

    assert PAYAI_FACILITATOR_URL == PAYAI_URL
    assert _bare_settings(PAYAI_FACILITATOR_URL).use_payai is True


# --- description length -------------------------------------------------------

def test_all_descriptions_under_500_chars():
    settings = load_settings()
    manifest = x402_manifest(settings, "https://example.test")
    descriptions = [
        main.VERDICT_DESCRIPTION,
        manifest["description"],
        *[r["description"] for r in manifest["resources"]],
    ]
    for d in descriptions:
        assert len(d) < 500, f"description too long ({len(d)} chars): {d[:60]}…"
    # The quality signal the Oct 2026 crawl found 94% of listings missing.
    assert "Use this when" in main.VERDICT_DESCRIPTION


# --- bazaar extension ----------------------------------------------------------

def test_extension_passes_sdk_validators():
    from x402.extensions.bazaar import (
        validate_discovery_extension,
        validate_discovery_extension_spec,
    )

    ext = bazaar_extension()
    result = validate_discovery_extension(ext)
    assert result.valid, result.errors
    spec = validate_discovery_extension_spec(ext)
    assert spec.valid, spec.errors


def test_routes_carry_the_same_extension():
    route = main.routes["POST /v1/verdict"]
    assert route.extensions["bazaar"] == bazaar_extension()


def test_output_schema_types_every_field():
    props = VERDICT_OUTPUT_SCHEMA["properties"]
    assert set(VERDICT_OUTPUT_SCHEMA["required"]) == {
        "token", "verdict", "score", "signals", "take", "market",
    }
    assert props["verdict"]["enum"] == ["SOLID", "CAUTION", "DANGER"]
    assert props["score"]["type"] == "number"
    signal_items = props["signals"]["items"]
    assert set(signal_items["required"]) == {"name", "score", "weight", "detail"}
    assert signal_items["properties"]["score"]["type"] == "integer"
    assert signal_items["properties"]["detail"]["type"] == "string"
    market = props["market"]["properties"]
    assert market["price_usd"]["type"] == ["string", "null"]
    assert market["txns_24h"]["type"] == "object"
    # And it must be reachable from the extension itself, not hollow.
    ext = bazaar_extension()
    assert ext["info"]["output"]["schema"] == VERDICT_OUTPUT_SCHEMA
    assert ext["schema"]["properties"]["output"]["properties"]["example"] == (
        VERDICT_OUTPUT_SCHEMA
    )


def test_example_validates_against_output_schema():
    jsonschema.validate(instance=VERDICT_OUTPUT_EXAMPLE, schema=VERDICT_OUTPUT_SCHEMA)


def test_example_stays_under_settled_sample_size_limit():
    # Settled compares a delivered response against the listing's
    # sample, but its parser drops samples above ~1.5 KB and grades
    # purchases nothing_to_compare instead. Fleet evidence 2026-10-25:
    # every sample <=1,453 serialized bytes was kept (RouteVerdict
    # /v1/check at 1,453 is the largest kept); this example at 1,721
    # and ListSmith /fix at 4,972 were dropped. Pin the size so the
    # sample cannot silently grow back over the line.
    import json

    assert len(json.dumps(VERDICT_OUTPUT_EXAMPLE)) <= 1450


def test_engine_output_validates_against_output_schema():
    # The dead-market fixture pair from test_verdict.py, assembled exactly
    # the way analyze() assembles its return value.
    pair = {
        "liquidity": {"usd": 259_106},
        "volume": {"h24": 0.01},
        "txns": {"h24": {"buys": 1, "sells": 2}},
        "priceChange": {"h24": 0.55},
        "pairCreatedAt": int(time.time() * 1000) - 300 * DAY_MS,
        "priceUsd": "0.0003238",
        "dexId": "uniswap",
        "url": "https://dexscreener.com/base/example",
        "baseToken": {"symbol": "BASE", "name": "Base King"},
    }
    signals = compute_signals(pair)
    score, verdict = score_signals(signals)
    score, verdict = apply_dead_market_guard(score, verdict, pair)
    result = {
        "token": {
            "address": "0x2821d96B5FC81aEC3763340b375a846a89A3Ec1b",
            "symbol": "BASE",
            "name": "Base King",
            "chain": "base",
        },
        "verdict": verdict,
        "score": score,
        "signals": signals,
        "take": write_take("BASE", "Base King", pair, signals, score, verdict),
        "market": {
            "price_usd": pair.get("priceUsd"),
            "liquidity_usd": (pair.get("liquidity") or {}).get("usd"),
            "volume_24h_usd": (pair.get("volume") or {}).get("h24"),
            "price_change_24h_pct": (pair.get("priceChange") or {}).get("h24"),
            "txns_24h": ((pair.get("txns") or {}).get("h24") or {}),
            "dex": pair.get("dexId"),
            "pair_url": pair.get("url"),
        },
    }
    jsonschema.validate(instance=result, schema=VERDICT_OUTPUT_SCHEMA)


# --- discovery surfaces stay consistent ---------------------------------------

@pytest.fixture(scope="module")
def client():
    return TestClient(main.app, raise_server_exceptions=False)


def test_discovery_endpoints_live_and_consistent(client):
    manifest = client.get("/.well-known/x402")
    assert manifest.status_code == 200
    body = manifest.json()
    resource = body["resources"][0]
    assert resource["inputSchema"]["required"] == ["address"]
    assert resource["outputSchema"] == VERDICT_OUTPUT_SCHEMA
    assert len(body["description"]) < 500

    assert client.get("/llms.txt").status_code == 200
    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    assert "/v1/verdict" in openapi.json()["paths"]
