"""Agent-discovery surfaces: x402 manifest, skill.md, llms.txt.

Indexers (x402scan, Bazaar, agentic.market) read these alongside the
live 402 responses, so they must stay consistent with main.py pricing.
"""

from __future__ import annotations

from .config import Settings


# x402 v2 "exact"-scheme asset for USDC on Base mainnet (6 decimals) — the
# real ERC-20 contract address, matching the asset the x402 SDK puts in the
# live 402 payment-required challenge (0x833589…2913). The manifest must
# mirror the challenge exactly or directory validators reject the mismatch.
USDC_ASSET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


def _price_to_units(price_usdc: str) -> str:
    """'$0.02' -> '20000' (USDC smallest units, 6 decimals)."""
    return str(int(round(float(price_usdc.replace("$", "").strip()) * 1_000_000)))


# --- Bazaar discovery schemas -------------------------------------------------
# Full typed JSON Schemas for the /v1/verdict request and response. The
# response schema types EVERY field the engine returns — catalog quality
# ranking weighs complete schemas, and a hollow {"type", "example"} output
# is the norm this service deliberately does not follow. A test validates
# the real engine output against VERDICT_OUTPUT_SCHEMA so the two can
# never drift apart.

VERDICT_INPUT_SCHEMA = {
    "type": "object",
    "required": ["address"],
    "properties": {
        "address": {
            "type": "string",
            "description": "Token contract address (0x...)",
        },
        "chain": {
            "type": "string",
            "enum": ["base", "ethereum", "solana"],
            "default": "base",
            "description": "Chain label: base | ethereum | solana",
        },
    },
}

VERDICT_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["token", "verdict", "score", "signals", "take", "market"],
    "properties": {
        "token": {
            "type": "object",
            "required": ["address", "symbol", "name", "chain"],
            "properties": {
                "address": {"type": "string"},
                "symbol": {"type": "string"},
                "name": {"type": "string"},
                "chain": {"type": "string"},
            },
        },
        "verdict": {"type": "string", "enum": ["SOLID", "CAUTION", "DANGER"]},
        "score": {
            "type": "number",
            "minimum": 0,
            "maximum": 100,
            "description": "Risk score: 0 (safest) to 100 (riskiest)",
        },
        "signals": {
            "type": "array",
            "minItems": 6,
            "maxItems": 6,
            "items": {
                "type": "object",
                "required": ["name", "score", "weight", "detail"],
                "properties": {
                    "name": {"type": "string"},
                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "weight": {"type": "number"},
                    "detail": {"type": "string"},
                },
            },
        },
        "take": {"type": "string", "description": "Written analyst summary"},
        "market": {
            "type": "object",
            "required": [
                "price_usd",
                "liquidity_usd",
                "volume_24h_usd",
                "price_change_24h_pct",
                "txns_24h",
                "dex",
                "pair_url",
            ],
            "properties": {
                "price_usd": {"type": ["string", "null"]},
                "liquidity_usd": {"type": ["number", "null"]},
                "volume_24h_usd": {"type": ["number", "null"]},
                "price_change_24h_pct": {"type": ["number", "null"]},
                "txns_24h": {
                    "type": "object",
                    "properties": {
                        "buys": {"type": "integer"},
                        "sells": {"type": "integer"},
                    },
                },
                "dex": {"type": ["string", "null"]},
                "pair_url": {"type": ["string", "null"]},
            },
        },
    },
}

# Full example response, produced by the real scoring engine
# (compute_signals -> score_signals -> dead-market guard -> write_take)
# run on the dead-market fixture pair used in tests/test_verdict.py
# (BASE / Base King: $259,106 liquidity, ~$0 volume, 3 txns in 24h) — the
# same dormant-market case as the first captured verdict. It is an
# engine-computed example, regenerated if the response shape changes;
# tests validate it against VERDICT_OUTPUT_SCHEMA.
VERDICT_OUTPUT_EXAMPLE = {
    "token": {
        "address": "0x2821d96B5FC81aEC3763340b375a846a89A3Ec1b",
        "symbol": "BASE",
        "name": "Base King",
        "chain": "base",
    },
    "verdict": "CAUTION",
    "score": 35.0,
    "signals": [
        {
            "name": "Liquidity depth",
            "score": 5,
            "weight": 3,
            "detail": "Deep liquidity ($259,106) — the book can absorb real selling.",
        },
        {
            "name": "Volume churn",
            "score": 60,
            "weight": 2,
            "detail": "24h volume is just $0 — effectively no market.",
        },
        {
            "name": "Sell pressure",
            "score": 60,
            "weight": 2,
            "detail": "67% sells vs 33% buys — clear distribution.",
        },
        {
            "name": "Price action",
            "score": 10,
            "weight": 2,
            "detail": "24h price action (+0.6%) is unremarkable.",
        },
        {
            "name": "Trader breadth",
            "score": 70,
            "weight": 1,
            "detail": "Only 3 transactions in 24h — a handful of wallets IS the market.",
        },
        {
            "name": "Pair age",
            "score": 5,
            "weight": 1,
            "detail": "Pair has meaningful history.",
        },
    ],
    "take": (
        "BASE (Base King) trades at $0.0003238 on uniswap with $259.1k in "
        "liquidity. Trading has effectively stopped — $0 of volume across "
        "3 transactions in 24h — so this is a dormant book, not a live "
        "market, whatever the liquidity figure says. Verdict: CAUTION — "
        "tradable at most in small size, and the signals above are why. "
        "Don't chase green candles. Not checked: contract honeypot "
        "simulation and holder concentration (need a paid indexer) — this "
        "verdict covers market structure only. Chart: "
        "https://dexscreener.com/base/0x2821d96b5fc81aec3763340b375a846a89a3ec1b"
    ),
    "market": {
        "price_usd": "0.0003238",
        "liquidity_usd": 259106,
        "volume_24h_usd": 0.01,
        "price_change_24h_pct": 0.55,
        "txns_24h": {"buys": 1, "sells": 2},
        "dex": "uniswap",
        "pair_url": "https://dexscreener.com/base/0x2821d96b5fc81aec3763340b375a846a89a3ec1b",
    },
}

VERDICT_INPUT_EXAMPLE = {
    "address": "0x2821d96B5FC81aEC3763340b375a846a89A3Ec1b",
    "chain": "base",
}


def bazaar_extension() -> dict:
    """The x402 Bazaar discovery extension for POST /v1/verdict, in the
    canonical v2 shape: `info` carries the values (example input AND a
    full example output with its typed schema); `schema` is the JSON
    Schema OF THE INFO OBJECT (properties input/output), which is what
    facilitator-side validators check the info against. Mirrors the
    output of the SDK's declare_discovery_extension for a body route,
    with info.input.method present at rest."""
    return {
        "info": {
            "input": {
                "type": "http",
                "method": "POST",
                "bodyType": "json",
                "body": VERDICT_INPUT_EXAMPLE,
            },
            "output": {
                "type": "json",
                "example": VERDICT_OUTPUT_EXAMPLE,
                "schema": VERDICT_OUTPUT_SCHEMA,
            },
        },
        "schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "input": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "const": "http"},
                        "method": {"type": "string", "enum": ["POST", "PUT", "PATCH"]},
                        "bodyType": {"type": "string", "enum": ["json", "form-data", "text"]},
                        "body": VERDICT_INPUT_SCHEMA,
                    },
                    "required": ["type", "method", "bodyType", "body"],
                    "additionalProperties": False,
                },
                "output": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "example": VERDICT_OUTPUT_SCHEMA,
                        "schema": {"type": "object"},
                    },
                    "required": ["type"],
                },
            },
            "required": ["input"],
        },
    }


def x402_manifest(settings: Settings, base_url: str) -> dict:
    # accepts[] mirrors the live 402 challenge so directory validators
    # (Agent Bazaar, x402scan) accept the manifest.
    accepts = [
        {
            "scheme": "exact",
            "network": settings.network,
            "amount": _price_to_units(settings.price_usdc),
            "asset": USDC_ASSET,
            "payTo": settings.pay_to,
            "maxTimeoutSeconds": 300,
        }
    ]
    if settings.solana_enabled:
        accepts.append(
            {
                "scheme": "exact",
                "network": settings.solana_network,
                "amount": _price_to_units(settings.price_usdc),
                "asset": settings.solana_usdc_mint,
                "payTo": settings.solana_pay_to,
                "maxTimeoutSeconds": 300,
            }
        )
    manifest = {
        "x402Version": 2,
        "service": settings.service_name,
        "description": (
            "Send a token contract address, get a risk verdict. Returns "
            "SOLID / CAUTION / DANGER with a 0-100 score, six weighted "
            "market-structure signals and a written analyst take. Built for "
            "AI agents screening tokens — no API key, no signup, "
            f"{settings.price_usdc} USDC per call. "
            "Use this when an agent is about to buy, swap, or route funds "
            "into a token and needs a fast risk screen first."
        ),
        "network": settings.network,
        "payTo": settings.pay_to,
        "resources": [
            {
                "resource": f"{base_url}/v1/verdict",
                "method": "POST",
                "price": settings.price_usdc,
                "scheme": "exact",
                "mimeType": "application/json",
                "description": (
                    "Token risk verdict: SOLID / CAUTION / DANGER with score, "
                    "six signals and a written take. Send a contract address. "
                    "Use this when screening a token before buying, swapping, "
                    "or routing funds into it."
                ),
                "tags": ["tokens", "defi", "risk", "research", "agents"],
                "accepts": accepts,
                "inputSchema": VERDICT_INPUT_SCHEMA,
                "outputSchema": VERDICT_OUTPUT_SCHEMA,
            },
            {
                "resource": f"{base_url}/quote",
                "method": "GET",
                "price": "$0.00",
                "description": "Free price quote for the paid endpoint",
            },
        ],
    }
    if settings.solana_enabled:
        manifest["networks"] = [settings.network, settings.solana_network]
    return manifest


def _solana_note(settings: Settings) -> str:
    if not settings.solana_enabled:
        return ""
    return (
        f" Also payable in USDC on Solana mainnet (`{settings.solana_network}`) "
        f"to `{settings.solana_pay_to}` — same price, same x402 v2 exact scheme."
    )


def skill_md(settings: Settings, base_url: str) -> str:
    return f"""# {settings.service_name} — token risk verdicts (x402)

Send a token contract address, get a risk verdict. Pay {settings.price_usdc} USDC
per call on Base (`{settings.network}`) via the x402 protocol — no API key needed.{_solana_note(settings)}

## Paid endpoint

`POST {base_url}/v1/verdict`

Body (JSON):
- `address` (required): token contract address, e.g. `0x2821d96B5FC81aEC3763340b375a846a89A3Ec1b`
- `chain` (optional, default `base`): `base` | `ethereum` | `solana`

Returns: `verdict` (SOLID / CAUTION / DANGER), `score` (0-100), `signals`
(six weighted market-structure signals with details), `take` (written analyst
summary), and raw `market` data (price, liquidity, volume, price change, txns).

Pay with any x402 v2 client (e.g. `x402-fetch`): call the endpoint, get a 402
challenge, sign, retry with the payment header. Settlement is USDC on the
network you paid on (Base, or Solana when the Solana rail is enabled).

## Free endpoints

- `GET {base_url}/quote` — current price, network, payTo
- `GET {base_url}/health` — liveness
- `GET {base_url}/.well-known/x402` — machine-readable manifest

## Honest limits

Market-structure analysis only. Not checked: contract honeypot simulation and
holder concentration (need a paid indexer). The verdict says what the market
looks like, not what the contract does.
"""


def llms_txt(settings: Settings, base_url: str) -> str:
    networks = settings.network
    if settings.solana_enabled:
        networks = f"{settings.network} or {settings.solana_network} (Solana)"
    return f"""# {settings.service_name}

Paid API: token risk verdicts. Price: {settings.price_usdc} USDC per call, x402 v2, network {networks}.

## Endpoints

- POST {base_url}/v1/verdict — paid. Body: {{"address": str, "chain"?: str}}
- GET {base_url}/quote — free price quote
- GET {base_url}/health — free liveness check
- GET {base_url}/.well-known/x402 — manifest
"""
