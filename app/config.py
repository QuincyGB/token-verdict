"""Settings for the token-verdict x402 service."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger("token-verdict.config")

BASE_MAINNET = "eip155:8453"
SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"

# His payout wallet on Base (EVM). Overridable via X402_PAY_TO.
OWNER_WALLET = "0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F"
PLACEHOLDER_WALLETS = {"0x0000000000000000000000000000000000000000"}

# His Solana wallet (receiving only — the service never holds a Solana key
# and never signs; the facilitator settles SPL transfers to this address).
SOLANA_OWNER_WALLET = "J6zMrhY1C4rTuH71dMkuVVirWr5eXCRrUg1SQ7qY7si"
# USDC mint on Solana mainnet (Circle SPL USDC, 6 decimals).
SOLANA_USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# ultravioletadao public x402 facilitator (no keys). Verified via /supported:
# serves eip155:8453 (Base) and eip155:5042 (Arc) with the exact scheme.
FACILITATOR_URL = "https://facilitator.ultravioletadao.xyz"

# DEXScreener free API (no key) — token market data.
DEXSCREENER_API = "https://api.dexscreener.com"


@dataclass(frozen=True)
class Settings:
    pay_to: str
    price_usdc: str  # e.g. "$0.02" — string format the x402 SDK expects
    network: str
    facilitator_url: str
    service_name: str
    fetch_timeout_s: float
    port: int
    # Solana rail (second x402 payment network; off unless enabled).
    solana_enabled: bool = False
    solana_pay_to: str = SOLANA_OWNER_WALLET
    solana_network: str = SOLANA_MAINNET
    solana_usdc_mint: str = SOLANA_USDC_MINT
    solana_rpc_url: str | None = None  # optional: adds blockhash hints to 402s
    # RapidAPI gateway shared secret (optional). Only used to gate the
    # /v1/verdict/rapidapi route; unset = route disabled. See
    # rapidapi-bypass-spec (platform-signups pack).
    rapidapi_proxy_secret: str | None = None


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    pay_to = os.environ.get("X402_PAY_TO", OWNER_WALLET).strip()
    if pay_to in PLACEHOLDER_WALLETS:
        raise RuntimeError("X402_PAY_TO is a placeholder address — set a real wallet.")
    if not (pay_to.startswith("0x") and len(pay_to) == 42):
        raise RuntimeError(f"X402_PAY_TO does not look like an EVM address: {pay_to!r}")

    network = os.environ.get("X402_NETWORK", BASE_MAINNET).strip()
    facilitator_url = os.environ.get("X402_FACILITATOR_URL", FACILITATOR_URL).strip()

    solana_enabled = _truthy(os.environ.get("SOLANA_RAIL_ENABLED"))
    solana_pay_to = os.environ.get("X402_SOLANA_PAY_TO", SOLANA_OWNER_WALLET).strip()
    if solana_enabled:
        from x402.mechanisms.svm.utils import validate_svm_address

        if not validate_svm_address(solana_pay_to):
            raise RuntimeError(f"X402_SOLANA_PAY_TO is not a valid Solana address: {solana_pay_to!r}")

    rapidapi_proxy_secret = os.environ.get("RAPIDAPI_PROXY_SECRET", "").strip() or None

    return Settings(
        pay_to=pay_to,
        price_usdc=os.environ.get("X402_PRICE_USDC", "$0.02").strip(),
        network=network,
        facilitator_url=facilitator_url,
        service_name=os.environ.get("X402_SERVICE_NAME", "token-verdict").strip(),
        fetch_timeout_s=float(os.environ.get("X402_FETCH_TIMEOUT_S", "15")),
        port=int(os.environ.get("PORT", "3000")),
        solana_enabled=solana_enabled,
        solana_pay_to=solana_pay_to,
        solana_network=os.environ.get("X402_SOLANA_NETWORK", SOLANA_MAINNET).strip(),
        solana_usdc_mint=os.environ.get("X402_SOLANA_USDC_MINT", SOLANA_USDC_MINT).strip(),
        solana_rpc_url=os.environ.get("SOLANA_RPC_URL", "").strip() or None,
        rapidapi_proxy_secret=rapidapi_proxy_secret,
    )
