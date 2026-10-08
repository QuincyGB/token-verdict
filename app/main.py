"""Token Verdict — x402 pay-per-call token risk analysis API.

Paid:   POST /v1/verdict   (USDC via x402 v2 "exact" scheme on Base)
Free:   GET  /health, /quote, /.well-known/x402, /skill.md, /llms.txt
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption, RouteConfig
from x402.http.middleware import fastapi_payment_middleware
from x402.mechanisms.evm.exact.server import ExactEvmScheme
from x402.server import x402ResourceServer

from .config import load_settings
from .discovery import llms_txt, skill_md, x402_manifest
from .verdict import TokenNotFound, analyze

log = logging.getLogger("token-verdict")
logging.basicConfig(level=logging.INFO)

settings = load_settings()  # raises if payout wallet is a placeholder

# --- x402 wiring -------------------------------------------------------------
# Keyless public facilitator (ultravioletadao) — verified to settle Base.
facilitator = HTTPFacilitatorClient(FacilitatorConfig(url=settings.facilitator_url))
log.info("facilitator: %s (%s)", settings.facilitator_url, settings.network)

resource_server = x402ResourceServer(facilitator)
resource_server.register(settings.network, ExactEvmScheme())

VERDICT_DESCRIPTION = (
    "Send a token contract address, get a risk verdict. Returns SOLID / "
    "CAUTION / DANGER with a 0-100 score, six weighted market-structure "
    "signals (liquidity depth, volume churn, sell pressure, price action, "
    "trader breadth, pair age) and a written analyst take. Built for AI "
    "agents screening tokens before touching them."
)

routes = {
    "POST /v1/verdict": RouteConfig(
        accepts=[
            PaymentOption(
                scheme="exact",
                pay_to=settings.pay_to,
                price=settings.price_usdc,
                network=settings.network,
            ),
        ],
        description=VERDICT_DESCRIPTION,
        mime_type="application/json",
        service_name=settings.service_name,  # ≤32 chars, forwarded to Bazaar
        tags=["tokens", "defi", "risk", "research", "agents"],
        extensions={
            "bazaar": {
                "info": {
                    "input": {
                        "type": "http",
                        "method": "POST",
                        "bodyType": "json",
                        "body": {
                            "address": "0x4200000000000000000000000000000000000042",
                            "chain": "base",
                        },
                    },
                    "output": {
                        "type": "application/json",
                        "example": {
                            "verdict": "SOLID",
                            "score": 12.5,
                            "take": "WETH (Wrapped Ether) trades at $...",
                        },
                    },
                },
                "schema": {
                    "type": "object",
                    "required": ["address"],
                    "properties": {
                        "address": {"type": "string"},
                        "chain": {"type": "string"},
                    },
                },
            }
        },
    ),
}

# --- app ---------------------------------------------------------------------
app = FastAPI(title="token-verdict", version="1.0.0")


@app.middleware("http")
async def x402_middleware(request: Request, call_next):
    return await fastapi_payment_middleware(routes, resource_server)(request, call_next)


class VerdictRequest(BaseModel):
    address: str = Field(description="Token contract address (0x...)")
    chain: str = Field(default="base", description="Chain id label: base | ethereum | solana")


@app.get("/health")
async def health():
    return {"ok": True, "service": settings.service_name}


@app.get("/quote")
async def quote():
    """Free: what the paid endpoint costs and where it settles."""
    return {
        "service": settings.service_name,
        "endpoint": "/v1/verdict",
        "price": settings.price_usdc,
        "asset": "USDC",
        "network": settings.network,
        "payTo": settings.pay_to,
        "protocol": "x402 v2 (exact scheme)",
    }


@app.get("/.well-known/x402")
async def well_known(request: Request):
    base = str(request.base_url).rstrip("/")
    # Railway terminates TLS at the edge; indexers need the public https URL.
    if base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    return JSONResponse(x402_manifest(settings, base))


@app.get("/skill.md", response_class=PlainTextResponse)
async def skill(request: Request):
    base = str(request.base_url).rstrip("/")
    return skill_md(settings, base)


@app.get("/llms.txt", response_class=PlainTextResponse)
async def llms(request: Request):
    base = str(request.base_url).rstrip("/")
    return llms_txt(settings, base)


@app.post("/v1/verdict")
async def verdict(req: VerdictRequest):
    """Paid endpoint — unreachable until the x402 middleware verifies payment."""
    address = req.address.strip()
    if not address.startswith("0x") or len(address) != 42:
        return JSONResponse({"error": "address must be a 0x contract address"}, status_code=422)
    try:
        result = await analyze(address, req.chain)
    except TokenNotFound as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except Exception as e:  # noqa: BLE001 — never leak tracebacks to payers
        log.exception("verdict failed for %s", address)
        return JSONResponse({"error": f"analysis failed: {e}"}, status_code=502)
    return result


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, reload=False)


if __name__ == "__main__":
    main()
