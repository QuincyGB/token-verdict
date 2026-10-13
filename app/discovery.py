"""Agent-discovery surfaces: x402 manifest, skill.md, llms.txt.

Indexers (x402scan, Bazaar, agentic.market) read these alongside the
live 402 responses, so they must stay consistent with main.py pricing.
"""

from __future__ import annotations

from .config import Settings


# x402 v2 "exact"-scheme asset for USDC (6 decimals). This is the canonical
# asset address the x402 SDK puts in the live 402 payment-required challenge.
USDC_ASSET = "0x3600000000000000000000000000000000000000"


def _price_to_units(price_usdc: str) -> str:
    """'$0.02' -> '20000' (USDC smallest units, 6 decimals)."""
    return str(int(round(float(price_usdc.replace("$", "").strip()) * 1_000_000)))


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
            f"{settings.price_usdc} USDC per call."
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
                    "six signals and a written take. Send a contract address."
                ),
                "tags": ["tokens", "defi", "risk", "research", "agents"],
                "accepts": accepts,
                "inputSchema": {
                    "type": "object",
                    "required": ["address"],
                    "properties": {
                        "address": {"type": "string", "description": "Token contract address (0x...)"},
                        "chain": {
                            "type": "string",
                            "default": "base",
                            "description": "Chain label: base | ethereum | solana",
                        },
                    },
                },
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
