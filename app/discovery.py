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

# x402scan ownership proof: an EIP-191 personal_sign of this service's bare
# origin URL ("https://token-verdict-production.up.railway.app") by the payTo
# wallet, signed by the owner on 2026-10-24. Published in the manifest below
# and in the OpenAPI x-discovery / x-agentcash-provenance root keys (see
# main.py) so x402scan can verify control of the payment address. This is
# publish-and-verify: the proofs must stay published permanently — removing
# them un-verifies the x402scan listing. DO NOT REMOVE.
OWNERSHIP_PROOFS = [
    "0xabf6bc4b30ac8f7c9ada7b8797400e0eb2153b5b2c460c0cfb8086373f31df12498d67e2b7bef67064387abad05b0d6f0180591db4a19f8b64b48d6f74de8f2d1c"
]


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
            "detail": "Deep liquidity ($259,106) — absorbs real selling.",
        },
        {
            "name": "Volume churn",
            "score": 60,
            "weight": 2,
            "detail": "24h volume is $0 — effectively no market.",
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
            "detail": "3 transactions in 24h — a handful of wallets is the market.",
        },
        {
            "name": "Pair age",
            "score": 5,
            "weight": 1,
            "detail": "Pair has meaningful history.",
        },
    ],
    # The take below is the real capture's take with its prose trimmed
    # (same facts, same numbers — the chart URL already lives in
    # market.pair_url). Reason: Settled's listing parser drops output
    # samples above ~1.5 KB and then grades scout purchases
    # nothing_to_compare ("the listing has no sample response to
    # compare against") — at 1,721 bytes this example was dropped
    # while every fleet sample <=1,453 bytes was kept (evidence:
    # Settled receipts + its /discovery/resources copies, 2026-10-25).
    # test_bazaar_discovery pins the serialized size so the sample
    # cannot silently grow back over the line.
    "take": (
        "BASE (Base King) trades at $0.0003238 on uniswap; $259.1k "
        "liquidity. Trading has stopped — $0 volume across 3 transactions "
        "in 24h — a dormant book, not a live market. "
        "Verdict: CAUTION — small size at most. Not checked: honeypot "
        "simulation and holder concentration — market structure only."
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


def _extension(input_example: dict, input_schema: dict,
               output_example: dict, output_schema: dict) -> dict:
    """The canonical v2 Bazaar extension shape (see bazaar_extension)."""
    return {
        "info": {
            "input": {
                "type": "http",
                "method": "POST",
                "bodyType": "json",
                "body": input_example,
            },
            "output": {
                "type": "json",
                "example": output_example,
                "schema": output_schema,
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
                        "body": input_schema,
                    },
                    "required": ["type", "method", "bodyType", "body"],
                    "additionalProperties": False,
                },
                "output": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "example": output_schema,
                        "schema": {"type": "object"},
                    },
                    "required": ["type"],
                },
            },
            "required": ["input"],
        },
    }


# --- /v1/pre-trade schemas ------------------------------------------------------

_SIGNAL_SCHEMA = {
    "type": "object",
    "required": ["name", "score", "weight", "detail"],
    "properties": {
        "name": {"type": "string"},
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "weight": {"type": "number"},
        "detail": {"type": "string"},
    },
}

PRE_TRADE_INPUT_SCHEMA = {
    "type": "object",
    "required": ["token_address"],
    "properties": {
        "token_address": {
            "type": "string",
            "description": "Token contract address (0x...) the trade would buy or receive",
        },
        "chain": {
            "type": "string",
            "enum": ["base", "ethereum", "solana"],
            "default": "base",
            "description": "Chain of the token leg. Wallet/allowance legs are Base-only: supplying wallet_address requires chain base.",
        },
        "wallet_address": {
            "type": ["string", "null"],
            "description": "Optional counterparty / own wallet (Base 0x...) to check for sanctions, age, activity and poisoning signals",
        },
        "spender": {
            "type": ["string", "null"],
            "description": "Optional spender contract (Base 0x...); with wallet_address, adds the allowance leg for that spender",
        },
        "mode": {
            "type": "string",
            "enum": ["full", "compact"],
            "description": "Optional response mode, default 'full'. 'full' returns every leg in full (signals, market data, sanctions and poisoning detail). 'compact' returns the decision, the worst leg, and one summary per leg (verdict, score, headline reason) — the same computation, a smaller response.",
        },
    },
}

_TOKEN_LEG_SCHEMA = {
    "type": "object",
    "required": ["status"],
    "properties": {
        "status": {"type": "string", "enum": ["ok", "unknown"]},
        "token": {
            "type": ["object", "null"],
            "properties": {
                "address": {"type": "string"},
                "symbol": {"type": "string"},
                "name": {"type": "string"},
                "chain": {"type": "string"},
            },
        },
        "verdict": {"type": ["string", "null"], "enum": ["SOLID", "CAUTION", "DANGER", None]},
        "risk_score": {"type": ["number", "null"], "minimum": 0, "maximum": 100},
        "signals": {"type": "array", "items": _SIGNAL_SCHEMA},
        "take": {"type": ["string", "null"]},
        "detail": {"type": "string"},
    },
}

_WALLET_LEG_SCHEMA = {
    "type": "object",
    "required": ["status"],
    "properties": {
        "status": {"type": "string", "enum": ["ok", "unknown"]},
        "address": {"type": "string"},
        "verdict": {"type": ["string", "null"], "enum": ["SAFE", "CAUTION", "AVOID", None]},
        "risk_score": {"type": ["number", "null"], "minimum": 0, "maximum": 100},
        "sanctions": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["matched", "no_match", "unknown"]},
                "matched": {"type": ["boolean", "null"]},
                "snapshot_date": {"type": ["string", "null"]},
            },
        },
        "wallet_age_days": {"type": ["integer", "null"]},
        "first_activity": {"type": "string"},
        "outgoing_tx_count_nonce": {"type": ["integer", "null"]},
        "poisoning": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["signals_found", "none_found", "unknown"]},
                "signals": {"type": "array"},
            },
        },
        "reasons": {"type": "array"},
        "not_checked": {"type": "string"},
    },
}

_ALLOWANCE_LEG_SCHEMA = {
    "type": "object",
    "required": ["status"],
    "properties": {
        "status": {"type": "string", "enum": ["ok", "unknown"]},
        "owner": {"type": "string"},
        "spender": {"type": ["string", "null"]},
        "tokens_held_checked": {"type": ["integer", "string"]},
        "spenders_checked": {"type": "integer"},
        "allowance_checks_performed": {"type": "integer"},
        "active_approvals": {"type": ["array", "string"]},
        "unlimited_approvals": {"type": ["integer", "string"]},
        "max_risk": {"type": "string", "enum": ["high", "medium", "none", "unknown"]},
        "summary": {"type": "string"},
        "not_checked": {"type": "string"},
    },
}

# The compact leg summary (mode="compact"): one entry per leg that ran,
# in token / wallet / allowance order. `verdict` and `score` are the
# leg's own (the allowance leg has neither — its headline carries its
# summary); `headline` is the leg's headline reason, taken from the leg
# itself (worst signal, sanctions hit / biggest deduction, or the
# stated reason a leg could not be evaluated).
_LEG_SUMMARY_SCHEMA = {
    "type": "object",
    "required": ["leg", "status", "headline"],
    "properties": {
        "leg": {"type": "string", "enum": ["token", "wallet", "allowance"]},
        "status": {"type": "string", "enum": ["ok", "unknown"]},
        "verdict": {"type": ["string", "null"]},
        "score": {"type": ["number", "null"], "minimum": 0, "maximum": 100},
        "headline": {"type": "string"},
    },
}

PRE_TRADE_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["verdict", "score", "legs", "degraded", "not_checked"],
    "properties": {
        "verdict": {"type": "string", "enum": ["PROCEED", "CAUTION", "ABORT"]},
        "score": {
            "type": "number",
            "minimum": 0,
            "maximum": 100,
            "description": "Combined risk = the worst delivered leg's risk score",
        },
        "token": {"type": "object"},
        "wallet": {"type": ["string", "null"]},
        "spender": {"type": ["string", "null"]},
        "legs": {
            "anyOf": [
                {
                    "type": "object",
                    "required": ["token"],
                    "properties": {
                        "token": _TOKEN_LEG_SCHEMA,
                        "wallet": _WALLET_LEG_SCHEMA,
                        "allowance": _ALLOWANCE_LEG_SCHEMA,
                    },
                },
                {"type": "array", "items": _LEG_SUMMARY_SCHEMA},
            ],
            "description": "Full mode: an object keyed by leg name with each leg in full. Compact mode: an array of per-leg summaries (see worst_leg).",
        },
        "worst_leg": {
            "type": ["string", "null"],
            "enum": ["token", "wallet", None],
            "description": "Compact mode only: the leg that drove the combined outcome (a hard-abort source, else the highest-risk scored leg)",
        },
        "abort_reasons": {"type": "array", "items": {"type": "string"}},
        "degraded": {
            "type": "boolean",
            "description": "True when at least one leg could not be evaluated (verdict then caps at CAUTION)",
        },
        "not_checked": {"type": "string"},
        "mode": {"type": "string", "enum": ["full", "compact"],
                 "description": "The response mode that produced this result (echo of the request's mode, 'full' when omitted). The legs shape follows it."},
    },
}

PRE_TRADE_INPUT_EXAMPLE = {
    "token_address": "0x2821d96B5FC81aEC3763340b375a846a89A3Ec1b",
    "chain": "base",
    "wallet_address": "0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F",
    "spender": None,
}

# Representative example in the exact response shape (token leg values
# mirror the /v1/verdict example fixture; wallet leg mirrors the shape
# pinned by tests/test_wallet_legs.py). Tests validate it against
# PRE_TRADE_OUTPUT_SCHEMA.
PRE_TRADE_OUTPUT_EXAMPLE = {
    "verdict": "CAUTION",
    "score": 35.0,
    "token": VERDICT_OUTPUT_EXAMPLE["token"],
    "wallet": "0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F",
    "spender": None,
    "legs": {
        "token": {
            "status": "ok",
            "token": VERDICT_OUTPUT_EXAMPLE["token"],
            "verdict": "CAUTION",
            "risk_score": 35.0,
            "signals": VERDICT_OUTPUT_EXAMPLE["signals"],
            "take": VERDICT_OUTPUT_EXAMPLE["take"],
            "market": VERDICT_OUTPUT_EXAMPLE["market"],
        },
        "wallet": {
            "status": "ok",
            "address": "0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F",
            "verdict": "SAFE",
            "risk_score": 0,
            "sanctions": {
                "status": "no_match",
                "matched": False,
                "detail": "Address does not appear in the bundled OFAC SDN digital-currency snapshot.",
                "snapshot_date": "2026-10-21",
                "snapshot_source": "https://www.treasury.gov/ofac/downloads/sdn.csv",
                "snapshot_entries": 91,
            },
            "wallet_age_days": 357,
            "first_activity": "2025-11-02T14:03:11+00:00",
            "outgoing_tx_count_nonce": 137,
            "poisoning": {
                "status": "none_found",
                "signals": [],
                "detail": "No zero-value lookalike transfers in the recent history sampled.",
            },
            "reasons": [],
            "not_checked": "Not checked: the person or entity behind the address.",
        },
    },
    "abort_reasons": [],
    "degraded": False,
    "not_checked": "Not checked: contract honeypot simulation and holder concentration.",
    "mode": "full",
}

# The listing's example pair is a COMPACT request/response: Settled's
# extractor silently drops published output samples over ~1.5 KB
# serialized (measured 2026-10-25: 1,453 B kept, 1,721 B dropped), and
# an honest full-mode sample cannot fit — the token leg alone re-embeds
# the full verdict fixture. The compact response is a real, complete
# product (the decision, the worst leg, one summary per leg), so its
# real output is what the listing publishes; full mode stays the
# default for direct buyers. Tests pin this example byte-equal to a
# fresh compact run on the fixture legs, and under the size limit.
PRE_TRADE_COMPACT_INPUT_EXAMPLE = {**PRE_TRADE_INPUT_EXAMPLE, "mode": "compact"}

PRE_TRADE_COMPACT_OUTPUT_EXAMPLE = {
    "mode": "compact",
    "verdict": "CAUTION",
    "score": 35.0,
    "token": VERDICT_OUTPUT_EXAMPLE["token"],
    "wallet": "0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F",
    "spender": None,
    "worst_leg": "token",
    "legs": [
        {
            "leg": "token",
            "status": "ok",
            "verdict": "CAUTION",
            "score": 35.0,
            "headline": "3 transactions in 24h — a handful of wallets is the market.",
        },
        {
            "leg": "wallet",
            "status": "ok",
            "verdict": "SAFE",
            "score": 0,
            "headline": "No risk deductions — sanctions no_match, poisoning none_found.",
        },
    ],
    "abort_reasons": [],
    "degraded": False,
    "not_checked": (
        "Not checked: contract honeypot simulation and holder concentration "
        "(need a paid indexer), the person or entity behind the wallet, and "
        "approvals beyond the allowance leg. Leg-level limits are itemised "
        "in full mode's per-leg not_checked fields."
    ),
}


def bazaar_pre_trade_extension() -> dict:
    return _extension(
        PRE_TRADE_COMPACT_INPUT_EXAMPLE,
        PRE_TRADE_INPUT_SCHEMA,
        PRE_TRADE_COMPACT_OUTPUT_EXAMPLE,
        PRE_TRADE_OUTPUT_SCHEMA,
    )


# --- /v1/batch-verdict schemas ----------------------------------------------------

_BATCH_ENTRY_SCHEMA = {
    "type": "object",
    "required": ["address", "status", "verdict"],
    "properties": {
        "address": {"type": "string"},
        "status": {"type": "string", "enum": ["ok", "unknown"]},
        "symbol": {
            "type": ["string", "null"],
            "description": "Compact mode only: the token's symbol (null when the token is UNKNOWN)",
        },
        "verdict": {"type": "string", "enum": ["SOLID", "CAUTION", "DANGER", "UNKNOWN"]},
        "score": {"type": ["number", "null"], "minimum": 0, "maximum": 100},
        "token": {"type": ["object", "null"]},
        "signals": {"type": "array", "items": _SIGNAL_SCHEMA},
        "take": {"type": ["string", "null"]},
        "reason": {
            "type": ["string", "null"],
            "description": "Why the token is UNKNOWN (set only when status is unknown)",
        },
    },
}

BATCH_INPUT_SCHEMA = {
    "type": "object",
    "required": ["token_addresses"],
    "properties": {
        "token_addresses": {
            "type": "array",
            "minItems": 1,
            "maxItems": 25,
            "items": {"type": "string"},
            "description": "1-25 Base token contract addresses (0x...). Duplicates are screened once.",
        },
        "mode": {
            "type": "string",
            "enum": ["full", "compact"],
            "description": "Optional response mode, default 'full'. 'full' returns every per-token verdict in full (signals, take, market data). 'compact' returns per-token address, symbol, verdict and score (plus the reason for UNKNOWN entries) and the summary counts — the same screen, a smaller response.",
        },
    },
}

BATCH_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["chain", "requested_count", "unique_count", "evaluated_count",
                 "unknown_count", "summary", "results"],
    "properties": {
        "chain": {"type": "string", "const": "base"},
        "requested_count": {"type": "integer", "minimum": 1, "maximum": 25},
        "unique_count": {"type": "integer"},
        "evaluated_count": {"type": "integer"},
        "unknown_count": {"type": "integer"},
        "summary": {
            "type": "object",
            "required": ["SOLID", "CAUTION", "DANGER", "UNKNOWN"],
            "properties": {
                "SOLID": {"type": "integer"},
                "CAUTION": {"type": "integer"},
                "DANGER": {"type": "integer"},
                "UNKNOWN": {"type": "integer"},
            },
        },
        "results": {
            "type": "array",
            "items": _BATCH_ENTRY_SCHEMA,
            "description": "Evaluated tokens riskiest first; UNKNOWN entries last",
        },
        "not_checked": {"type": "string"},
        "mode": {"type": "string", "enum": ["full", "compact"],
                 "description": "The response mode that produced this result (echo of the request's mode, 'full' when omitted). Compact entries carry address/status/symbol/verdict/score/reason only."},
    },
}

BATCH_INPUT_EXAMPLE = {
    "token_addresses": [
        "0x2821d96B5FC81aEC3763340b375a846a89A3Ec1b",
        "0x000000000000000000000000000000000000dEaD",
    ]
}

# Representative example in the exact response shape: the first entry is
# the /v1/verdict example fixture verbatim; the second shows the honest
# UNKNOWN shape for a token with no fetchable market. Tests validate it
# against BATCH_OUTPUT_SCHEMA.
BATCH_OUTPUT_EXAMPLE = {
    "chain": "base",
    "requested_count": 2,
    "unique_count": 2,
    "evaluated_count": 1,
    "unknown_count": 1,
    "summary": {"SOLID": 0, "CAUTION": 1, "DANGER": 0, "UNKNOWN": 1},
    "results": [
        {
            "address": "0x2821d96B5FC81aEC3763340b375a846a89A3Ec1b",
            "status": "ok",
            "verdict": "CAUTION",
            "score": 35.0,
            "token": VERDICT_OUTPUT_EXAMPLE["token"],
            "signals": VERDICT_OUTPUT_EXAMPLE["signals"],
            "take": VERDICT_OUTPUT_EXAMPLE["take"],
            "market": VERDICT_OUTPUT_EXAMPLE["market"],
            "reason": None,
        },
        {
            "address": "0x000000000000000000000000000000000000dEaD",
            "status": "unknown",
            "verdict": "UNKNOWN",
            "score": None,
            "token": None,
            "signals": [],
            "take": None,
            "market": None,
            "reason": "No DEXScreener pair found for this token on base.",
        },
    ],
    "not_checked": "Per-token limits apply: market structure only.",
    "mode": "full",
}

# The listing's example pair is a COMPACT request/response, for the
# same Settled sample-size reason as /v1/pre-trade (see the note
# there): a full-mode entry alone is a complete per-token verdict and
# cannot fit the ~1.5 KB published-sample limit honestly. The compact
# response is a real, complete screen (per-token verdict + score in
# riskiest-first order, plus the summary counts), so its real output
# is what the listing publishes; full mode stays the default for
# direct buyers. Tests pin this example byte-equal to a fresh compact
# run on the fixture pair, and under the size limit.
BATCH_COMPACT_INPUT_EXAMPLE = {**BATCH_INPUT_EXAMPLE, "mode": "compact"}

BATCH_COMPACT_OUTPUT_EXAMPLE = {
    "mode": "compact",
    "chain": "base",
    "requested_count": 2,
    "unique_count": 2,
    "evaluated_count": 1,
    "unknown_count": 1,
    "summary": {"SOLID": 0, "CAUTION": 1, "DANGER": 0, "UNKNOWN": 1},
    "results": [
        {
            "address": "0x2821d96B5FC81aEC3763340b375a846a89A3Ec1b",
            "status": "ok",
            "symbol": "BASE",
            "verdict": "CAUTION",
            "score": 35.0,
            "reason": None,
        },
        {
            "address": "0x000000000000000000000000000000000000dEaD",
            "status": "unknown",
            "symbol": None,
            "verdict": "UNKNOWN",
            "score": None,
            "reason": "No DEXScreener pair found for this token on base.",
        },
    ],
    "not_checked": (
        "Per-token limits apply (see each result's take): market "
        "structure only — no contract honeypot simulation, no holder "
        "concentration."
    ),
}


def bazaar_batch_extension() -> dict:
    return _extension(
        BATCH_COMPACT_INPUT_EXAMPLE,
        BATCH_INPUT_SCHEMA,
        BATCH_COMPACT_OUTPUT_EXAMPLE,
        BATCH_OUTPUT_SCHEMA,
    )


def _base_accepts(settings: Settings, price_usdc: str) -> list[dict]:
    return [
        {
            "scheme": "exact",
            "network": settings.network,
            "amount": _price_to_units(price_usdc),
            "asset": USDC_ASSET,
            "payTo": settings.pay_to,
            "maxTimeoutSeconds": 300,
        }
    ]


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
        "ownershipProofs": OWNERSHIP_PROOFS,
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
                "resource": f"{base_url}/v1/pre-trade",
                "method": "POST",
                "price": settings.pre_trade_price_usdc,
                "scheme": "exact",
                "mimeType": "application/json",
                "description": (
                    "Combined go/no-go before a trade: PROCEED / CAUTION / "
                    "ABORT from the token's market verdict, the wallet's "
                    "sanctions/age/poisoning check, and (with a spender) its "
                    "ERC-20 approvals. Mode 'compact' returns the decision "
                    "plus per-leg summaries. Use this when an agent is about "
                    "to swap or pay and needs token + wallet + allowance "
                    "risk in one call."
                ),
                "tags": ["tokens", "wallets", "defi", "risk", "pre-trade", "agents"],
                "accepts": _base_accepts(settings, settings.pre_trade_price_usdc),
                "inputSchema": PRE_TRADE_INPUT_SCHEMA,
                "outputSchema": PRE_TRADE_OUTPUT_SCHEMA,
            },
            {
                "resource": f"{base_url}/v1/batch-verdict",
                "method": "POST",
                "price": settings.batch_verdict_price_usdc,
                "scheme": "exact",
                "mimeType": "application/json",
                "description": (
                    "Screen 1-25 Base tokens in one flat-fee call: per-token "
                    "SOLID / CAUTION / DANGER verdicts with scores, sorted "
                    "riskiest first, plus summary counts; unfetchable tokens "
                    "return UNKNOWN with a reason. Mode 'compact' returns "
                    "per-token verdict/score and the counts only. Use this "
                    "when an agent is screening a list of tokens before "
                    "trading any of them."
                ),
                "tags": ["tokens", "batch", "defi", "risk", "screening", "agents"],
                "accepts": _base_accepts(settings, settings.batch_verdict_price_usdc),
                "inputSchema": BATCH_INPUT_SCHEMA,
                "outputSchema": BATCH_OUTPUT_SCHEMA,
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

## More paid endpoints

`POST {base_url}/v1/pre-trade` — {settings.pre_trade_price_usdc} USDC, Base only.
One combined go/no-go before a trade: `verdict` PROCEED / CAUTION / ABORT with
a combined risk `score`. Body: `token_address` (required), `chain` (optional,
default `base`), `wallet_address` (optional — adds the wallet leg: OFAC
sanctions snapshot, age/activity, address-poisoning signals), `spender`
(optional — with a wallet, adds the allowance leg for that spender), `mode`
(optional: `"full"` default, or `"compact"`). A leg
that cannot be evaluated returns status `unknown` and caps the verdict at
CAUTION; if no leg can be evaluated the call fails with 502 and no payment
settles.

`POST {base_url}/v1/batch-verdict` — {settings.batch_verdict_price_usdc} USDC flat, Base only.
Body: `token_addresses` — 1 to 25 Base token addresses, `mode` (optional:
`"full"` default, or `"compact"`). Returns per-token
verdicts sorted riskiest first plus summary counts. A token that cannot be
fetched returns verdict UNKNOWN with a reason; if more than half the batch
cannot be fetched the call fails with 502 and no payment settles.

Both routes take `mode`. In compact mode the response is smaller and runs
the identical computation: pre-trade returns the decision, `worst_leg`, and
one summary per leg (`leg` / `status` / `verdict` / `score` / `headline` —
the leg's headline reason); batch-verdict returns per-token `address` /
`symbol` / `verdict` / `score` (plus `reason` for UNKNOWN entries) and the
summary counts, in the same riskiest-first order. Everything needed to act
is in the compact response; full mode adds the nested detail (signals,
market data, sanctions/poisoning breakdowns, takes). Same price either way.

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

- POST {base_url}/v1/verdict — paid {settings.price_usdc}. Body: {{"address": str, "chain"?: str}}
- POST {base_url}/v1/pre-trade — paid {settings.pre_trade_price_usdc}, Base. Body: {{"token_address": str, "chain"?: str, "wallet_address"?: str, "spender"?: str, "mode"?: "full"|"compact"}} — combined PROCEED / CAUTION / ABORT from token + wallet + allowance legs; compact returns the decision + worst leg + per-leg summaries
- POST {base_url}/v1/batch-verdict — paid {settings.batch_verdict_price_usdc} flat, Base. Body: {{"token_addresses": [str, ...], "mode"?: "full"|"compact"}} — 1-25 tokens, per-token verdicts riskiest first + summary counts; compact returns per-token verdict/score + counts only
- GET {base_url}/quote — free price quote
- GET {base_url}/health — free liveness check
- GET {base_url}/.well-known/x402 — manifest
"""
