"""Settings for the token-verdict x402 service."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger("token-verdict.config")

BASE_MAINNET = "eip155:8453"

# His payout wallet on Base (EVM). Overridable via X402_PAY_TO.
OWNER_WALLET = "0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F"
PLACEHOLDER_WALLETS = {"0x0000000000000000000000000000000000000000"}

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


def load_settings() -> Settings:
    pay_to = os.environ.get("X402_PAY_TO", OWNER_WALLET).strip()
    if pay_to in PLACEHOLDER_WALLETS:
        raise RuntimeError("X402_PAY_TO is a placeholder address — set a real wallet.")
    if not (pay_to.startswith("0x") and len(pay_to) == 42):
        raise RuntimeError(f"X402_PAY_TO does not look like an EVM address: {pay_to!r}")

    network = os.environ.get("X402_NETWORK", BASE_MAINNET).strip()
    facilitator_url = os.environ.get("X402_FACILITATOR_URL", FACILITATOR_URL).strip()

    return Settings(
        pay_to=pay_to,
        price_usdc=os.environ.get("X402_PRICE_USDC", "$0.02").strip(),
        network=network,
        facilitator_url=facilitator_url,
        service_name=os.environ.get("X402_SERVICE_NAME", "token-verdict").strip(),
        fetch_timeout_s=float(os.environ.get("X402_FETCH_TIMEOUT_S", "15")),
        port=int(os.environ.get("PORT", "3000")),
    )
