"""Token Verdict — x402 pay-per-call token risk analysis API.

Paid:   POST /v1/verdict   (USDC via x402 v2 "exact" scheme on Base)
Free:   GET  /health, /quote, /.well-known/x402, /skill.md, /llms.txt
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
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
                            "address": "0x2821d96B5FC81aEC3763340b375a846a89A3Ec1b",
                            "chain": "base",
                        },
                    },
                    "output": {
                        "type": "application/json",
                        "example": {
                            "verdict": "CAUTION",
                            "score": 35.0,
                            "take": "BASE (Base King) trades at $0.0003238 on uniswap with $259.1k in liquidity. Trading has effectively stopped — $0 of volume across 3 transactions in 24h — so this is a dormant book, not a live market, whatever the liquidity figure says. Verdict: CAUTION ...",
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


def _public_base(request: Request) -> str:
    """Public base URL of this service. Railway terminates TLS at the edge,
    so the request scheme arrives as http — indexers and agents need https."""
    base = str(request.base_url).rstrip("/")
    if base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    return base


LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Token Verdict — pay-per-call token risk verdicts for AI agents</title>
<meta name="description" content="Before an AI agent spends real money on a token, it pays $0.02 to ask Token Verdict first. SOLID / CAUTION / DANGER verdicts over x402 on Base. No accounts, no API keys: payment is the authentication.">
<style>
  body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
         max-width: 760px; margin: 40px auto; padding: 0 20px 60px;
         line-height: 1.55; color: #16202a; background: #fbfcfd; }
  h1 { font-size: 2rem; margin-bottom: .2em; }
  .tag { font-size: 1.1rem; color: #33475b; margin-top: 0; }
  code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  pre { background: #0f1b26; color: #dcebf5; padding: 14px 16px;
        border-radius: 8px; overflow-x: auto; font-size: .85rem; }
  table { border-collapse: collapse; width: 100%; margin: 1em 0; }
  td, th { border-bottom: 1px solid #dde5ec; padding: 6px 8px;
           text-align: left; vertical-align: top; }
  .pill { display: inline-block; background: #e7f0e9; color: #1d5c33;
          border-radius: 999px; padding: 2px 10px; font-size: .85rem; }
  a { color: #0b5cad; }
  .limits { background: #fdf6e5; border: 1px solid #ecd9a0;
            border-radius: 8px; padding: 10px 14px; }
</style>
</head>
<body>
<h1>Token Verdict</h1>
<p class="tag">Before an AI agent spends real money on a token, it pays
<strong>$0.02</strong> to ask Token Verdict first — a pay-per-call risk score
for the agent economy. No accounts, no API keys: <em>payment is the
authentication.</em></p>

<p><span class="pill">Live on Base mainnet</span>
<span class="pill">x402 v2 · exact scheme</span>
<span class="pill">$0.02 USDC / call</span></p>

<h2>What an agent gets</h2>
<p>One <code>POST /v1/verdict</code> with a token contract address returns:</p>
<ul>
  <li>a verdict — <strong>SOLID</strong>, <strong>CAUTION</strong>, or
      <strong>DANGER</strong></li>
  <li>a risk score from 0 to 100</li>
  <li>six weighted market-structure signals — liquidity depth, volume churn,
      sell pressure, price action, trader breadth, pair age — each scored and
      explained in plain English</li>
  <li>a short written analyst take, plus the raw market data the verdict was
      derived from, so any caller can re-check the math</li>
</ul>

<h2>How to call it</h2>
<pre>curl -X POST https://token-verdict-production.up.railway.app/v1/verdict \\
  -H 'content-type: application/json' \\
  -d '{"address":"0x2821d96B5FC81aEC3763340b375a846a89A3Ec1b","chain":"base"}'</pre>
<p>Unpaid calls return an HTTP 402 x402 challenge (price, asset, network,
pay-to wallet). Pay $0.02 USDC on Base with any x402 v2 client, retry with
the payment header, and the verdict comes back. There is no signup and no
key to manage — the settlement is the credential.</p>

<h2>Endpoints</h2>
<table>
  <tr><th>Route</th><th>Price</th><th>Purpose</th></tr>
  <tr><td><code>POST /v1/verdict</code></td><td>$0.02</td>
      <td>Token risk verdict (paid, x402)</td></tr>
  <tr><td><code>GET /quote</code></td><td>free</td>
      <td>Price, asset, network, pay-to wallet</td></tr>
  <tr><td><code>GET /.well-known/x402</code></td><td>free</td>
      <td>Machine-readable service manifest</td></tr>
  <tr><td><code>GET /skill.md</code></td><td>free</td>
      <td>Documentation written for agents</td></tr>
  <tr><td><code>GET /llms.txt</code></td><td>free</td>
      <td>Compact agent index</td></tr>
  <tr><td><code>GET /health</code></td><td>free</td><td>Liveness</td></tr>
</table>

<p>Also discoverable via the x402 Bazaar discovery extension embedded in the
402 challenge itself, and listed on Coinbase's Agent Bazaar (resource
21729). Source code:
<a href="https://github.com/QuincyGB/token-verdict">github.com/QuincyGB/token-verdict</a>
(public source — the scoring engine is deterministic and fully
inspectable, and the test suite ships with it).</p>

<div class="limits">
<strong>Honest limits:</strong> Token Verdict reads market structure. It does
not simulate the token contract for honeypots or measure holder
concentration — those need a paid indexer, and they are on the roadmap. The
verdict says what the market looks like, not what the contract does. Market
data, not financial advice.
</div>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def landing():
    return LANDING_HTML


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
    return JSONResponse(x402_manifest(settings, _public_base(request)))


@app.get("/skill.md", response_class=PlainTextResponse)
async def skill(request: Request):
    return skill_md(settings, _public_base(request))


@app.get("/llms.txt", response_class=PlainTextResponse)
async def llms(request: Request):
    return llms_txt(settings, _public_base(request))


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
