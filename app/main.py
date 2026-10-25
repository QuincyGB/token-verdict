"""Token Verdict — x402 pay-per-call token risk analysis API.

Paid:   POST /v1/verdict   (USDC via x402 v2 "exact" scheme on Base;
        Solana mainnet rail too when SOLANA_RAIL_ENABLED is set)
Free:   GET  /health, /quote, /.well-known/x402, /skill.md, /llms.txt,
        /example (one verdict end to end, for humans)
"""

from __future__ import annotations

import hmac
import html
import json
import logging
import os

from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption, RouteConfig
from x402.http.middleware import fastapi_payment_middleware
from x402.mechanisms.evm.exact.server import ExactEvmScheme
from x402.mechanisms.svm.exact.server import ExactSvmScheme as ExactSvmServerScheme
from x402.server import x402ResourceServer

from .config import load_settings
from .discovery import OWNERSHIP_PROOFS, bazaar_extension, llms_txt, skill_md, x402_manifest
from .solana_rail import SvmReplayGuard, make_after_settle_hook, make_before_verify_hook
from .verdict import TokenNotFound, analyze

log = logging.getLogger("token-verdict")
logging.basicConfig(level=logging.INFO)

settings = load_settings()  # raises if payout wallet is a placeholder

# --- x402 wiring -------------------------------------------------------------
# The facilitator is chosen by X402_FACILITATOR_URL alone (plus CDP keys for
# the CDP branch). Cataloging is settlement-driven per facilitator — each
# index lists only settlements through its OWN facilitator — so the env
# choice decides which catalog a settled payment feeds:
#   * CDP (api.cdp.coinbase.com, needs CDP_API_KEY_ID/SECRET): CDP Bazaar.
#   * PayAI (facilitator.payai.network, no key for exact): PayAI index.
#   * default keyless ultravioletadao: settles Base, feeds no major index.
# One deployment settles through one facilitator at a time; to appear in
# both indexes, settle at least one call under each configuration.
if settings.use_cdp:
    from .cdp_auth import cdp_facilitator_config

    assert settings.cdp_api_key_id and settings.cdp_api_key_secret
    facilitator = HTTPFacilitatorClient(
        cdp_facilitator_config(settings.cdp_api_key_id, settings.cdp_api_key_secret)
    )
    log.info("facilitator: CDP (Bazaar cataloging path)")
elif settings.use_payai:
    facilitator = HTTPFacilitatorClient(FacilitatorConfig(url=settings.facilitator_url))
    log.info("facilitator: PayAI (PayAI index cataloging path)")
else:
    facilitator = HTTPFacilitatorClient(FacilitatorConfig(url=settings.facilitator_url))
    log.info("facilitator: %s (%s)", settings.facilitator_url, settings.network)

resource_server = x402ResourceServer(facilitator)
resource_server.register(settings.network, ExactEvmScheme())

# --- Solana rail (env-gated; Base behaviour is unchanged when off) ----------
# Same facilitator: ultravioletadao's /supported lists x402 v2 exact on
# solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp alongside eip155:8453, so the one
# facilitator client settles both networks.
_accepts = [
    PaymentOption(
        scheme="exact",
        pay_to=settings.pay_to,
        price=settings.price_usdc,
        network=settings.network,
    ),
]

if settings.solana_enabled:
    resource_server.register(
        settings.solana_network,
        ExactSvmServerScheme(rpc_url=settings.solana_rpc_url),
    )
    _svm_replay_guard = SvmReplayGuard()
    resource_server.on_before_verify(
        make_before_verify_hook(
            _svm_replay_guard,
            solana_network=settings.solana_network,
            usdc_mint=settings.solana_usdc_mint,
        )
    )
    resource_server.on_after_settle(
        make_after_settle_hook(_svm_replay_guard, solana_network=settings.solana_network)
    )
    _accepts.append(
        PaymentOption(
            scheme="exact",
            pay_to=settings.solana_pay_to,
            price=settings.price_usdc,
            network=settings.solana_network,
        )
    )
    log.info(
        "solana rail enabled: %s payTo %s",
        settings.solana_network,
        settings.solana_pay_to,
    )

VERDICT_DESCRIPTION = (
    "Send a token contract address, get a risk verdict. Returns SOLID / "
    "CAUTION / DANGER with a 0-100 score, six weighted market-structure "
    "signals (liquidity depth, volume churn, sell pressure, price action, "
    "trader breadth, pair age) and a written analyst take. Built for AI "
    "agents screening tokens before touching them. Use this when an agent "
    "is about to buy, swap, or route funds into a token and needs a fast "
    "risk screen first."
)

routes = {
    "POST /v1/verdict": RouteConfig(
        accepts=_accepts,
        description=VERDICT_DESCRIPTION,
        mime_type="application/json",
        service_name=settings.service_name,  # ≤32 chars, forwarded to Bazaar
        tags=["tokens", "defi", "risk", "research", "agents"],
        extensions={"bazaar": bazaar_extension()},
    ),
}

# --- app ---------------------------------------------------------------------
app = FastAPI(title="token-verdict", version="1.0.0")


def _openapi_with_ownership_proofs():
    """Default OpenAPI document plus the x402scan ownership-proof root
    keys (x-discovery / x-agentcash-provenance). x402scan reads these to
    verify control of the payTo address — see OWNERSHIP_PROOFS in
    discovery.py; removing them un-verifies the listing."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    schema["x-discovery"] = {"ownershipProofs": OWNERSHIP_PROOFS}
    schema["x-agentcash-provenance"] = {"ownershipProofs": OWNERSHIP_PROOFS}
    app.openapi_schema = schema
    return schema


app.openapi = _openapi_with_ownership_proofs

# Build the payment middleware ONCE. Constructing it runs the SDK's
# initialize(), which synchronously fetches the facilitator's /supported
# document over the network — building it per request (as this module did
# originally) added a full external round-trip, on the event loop, to
# EVERY request including /health and the unpaid 402 challenge. That was
# the fleet's latency-variance problem (2026-10-24): Settled's latency
# component whipsawed 7/15 → 1/15 → 4/15 on a service that hadn't changed.
_payment_middleware = fastapi_payment_middleware(routes, resource_server)


@app.middleware("http")
async def x402_middleware(request: Request, call_next):
    return await _payment_middleware(request, call_next)


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

<p><strong>Worked example:</strong> <a href="/example">one complete verdict,
step by step</a> — discovery, the request, the real 402 challenge, the
payment, and the response.</p>

<h2>Endpoints</h2>
<table>
  <tr><th>Route</th><th>Price</th><th>Purpose</th></tr>
  <tr><td><code>POST /v1/verdict</code></td><td>$0.02</td>
      <td>Token risk verdict (paid, x402)</td></tr>
  <tr><td><code>GET /example</code></td><td>free</td>
      <td>One verdict end to end — worked example for humans</td></tr>
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


# --- /example: one verdict, end to end (for humans) ---------------------------
# The 402 challenge rendered here is the REAL challenge this service returns:
# resource + accepts are the live payment terms (same values as the capture
# saved on 2026-10-09 and re-verified against a live unpaid call on
# 2026-10-10). The single disclosed edit: the Bazaar extension's sample
# *output* is elided, because a sample verdict is exactly what this page
# refuses to publish.
#
# The verdict section is populated ONLY from a real paid capture: drop a
# verbatim paid response at app/example_verdict.json and it renders as-is.
# Until then the section states, plainly, that it is awaiting that capture.
# No mock verdicts, ever — a risk product faking its own example would be
# the story, not the demo.

_EXAMPLE_TOKEN = "0x2821d96B5FC81aEC3763340b375a846a89A3Ec1b"
_USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


def _load_example_verdict() -> dict | None:
    path = os.path.join(os.path.dirname(__file__), "example_verdict.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "verdict" in data and "signals" in data:
            return data
    except (OSError, ValueError):
        pass
    return None


_EXAMPLE_VERDICT = _load_example_verdict()


def _example_bazaar_extension() -> dict:
    """The live bazaar extension with ONLY the sample output elided —
    the schemas stay complete; the page's rule is about not publishing
    an example verdict as if it were a capture (see step 4)."""
    ext = bazaar_extension()
    ext["info"]["output"]["example"] = (
        "… (elided — see step 4: this page publishes only real captured verdicts)"
    )
    return ext


def _example_challenge() -> dict:
    return {
        "x402Version": 2,
        "error": "Payment required",
        "resource": {
            "url": "https://token-verdict-production.up.railway.app/v1/verdict",
            "description": VERDICT_DESCRIPTION,
            "mimeType": "application/json",
            "serviceName": "token-verdict",
            "tags": ["tokens", "defi", "risk", "research", "agents"],
        },
        "accepts": [
            {
                "scheme": "exact",
                "network": settings.network,
                "asset": _USDC_BASE,
                "amount": "20000",
                "payTo": settings.pay_to,
                "maxTimeoutSeconds": 300,
                "extra": {"name": "USD Coin", "version": "2"},
            }
        ],
        "extensions": {"bazaar": _example_bazaar_extension()},
    }


def _verdict_section() -> str:
    v = _EXAMPLE_VERDICT
    if v is None:
        return """
<h2>Step 4 — The verdict</h2>
<div class="pending">
<strong>Status: awaiting the first published capture.</strong> This section
is filled by a real paid call — the exact response the service returns once
the $0.02 settles — published here verbatim. We don't post mock verdicts: a
risk product that fakes its own example would be the story, not the demo.
</div>
<p>When the payment settles, the retried call returns JSON with:</p>
<ul>
  <li><code>verdict</code> — SOLID, CAUTION, or DANGER</li>
  <li><code>score</code> — risk score, 0 (safest) to 100 (riskiest)</li>
  <li><code>signals</code> — the six market-structure signals (liquidity
      depth, volume churn, sell pressure, price action, trader breadth,
      pair age), each with its own score, weight, and a plain-English
      detail line</li>
  <li><code>take</code> — a short written analyst summary</li>
  <li><code>market</code> — the raw market data the verdict was derived
      from, so any caller can re-check the math</li>
</ul>
<p>Every step above this one is live and free right now — the manifest, the
quote, and the 402 challenge. Only the verdict itself is paywalled, at
$0.02.</p>
"""
    esc = html.escape
    tok = v.get("token") or {}
    sym = ""
    if tok.get("symbol"):
        sym = f" ({esc(str(tok['symbol']))} — {esc(str(tok.get('name', '')))})"
    rows = []
    for s in v.get("signals") or []:
        rows.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                esc(str(s.get("name", ""))),
                esc(str(s.get("score", ""))),
                esc(str(s.get("weight", ""))),
                esc(str(s.get("detail", ""))),
            )
        )
    mkt = v.get("market") or {}
    pill_cls = {
        "SOLID": "v-solid",
        "CAUTION": "v-caution",
        "DANGER": "v-danger",
    }.get(str(v.get("verdict", "")).upper(), "pill")
    return f"""
<h2>Step 4 — The verdict (a real paid capture)</h2>
<p>This is the verbatim response of a real paid call for
<code>{esc(str(tok.get('address', _EXAMPLE_TOKEN)))}</code>{sym}:</p>
<p><span class="pill {pill_cls}">{esc(str(v.get('verdict', '')))}</span>
<strong>Risk score: {esc(str(v.get('score', '')))} / 100</strong></p>
<table>
  <tr><th>Signal</th><th>Score</th><th>Weight</th><th>Detail</th></tr>
  {''.join(rows)}
</table>
<p><strong>Analyst take:</strong> {esc(str(v.get('take', '')))}</p>
<p>The market data it was derived from: price ${esc(str(mkt.get('price_usd', '')))},
liquidity ${esc(str(mkt.get('liquidity_usd', '')))}, 24h volume
${esc(str(mkt.get('volume_24h_usd', '')))}, 24h price change
{esc(str(mkt.get('price_change_24h_pct', '')))}% on
{esc(str(mkt.get('dex', '')))}. Any caller can reproduce this response —
and re-check every number in it — for $0.02.</p>
"""


EXAMPLE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>One verdict, end to end — Token Verdict</title>
<meta name="description" content="A complete Token Verdict call, step by step: discovery, the request, the real HTTP 402 x402 challenge, the $0.02 payment on Base, and the verdict — published only from a real paid capture.">
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
  .v-solid { background: #e7f0e9; color: #1d5c33; }
  .v-caution { background: #fdf6e5; color: #8a6d00; }
  .v-danger { background: #fdecec; color: #8c1d2c; }
  a { color: #0b5cad; }
  .pending { background: #fdf6e5; border: 1px solid #ecd9a0;
             border-radius: 8px; padding: 10px 14px; }
</style>
</head>
<body>
<h1>One verdict, end to end</h1>
<p class="tag">Token Verdict is built for AI agents, so its
&ldquo;interface&rdquo; is a conversation between machines. Here is that
conversation, translated — the steps an agent runs, with the real data at
each step.</p>

<p>The scenario: an autonomous trading agent is about to buy the token at
<code>0x2821d96B5FC81aEC3763340b375a846a89A3Ec1b</code> on Base. Before it
spends anything, it asks Token Verdict first.</p>

<h2>Step 0 — Discovery (free)</h2>
<p>The agent finds the service with no human involved: a machine-readable
manifest at <a href="/.well-known/x402"><code>/.well-known/x402</code></a>,
documentation written for agents at <a href="/skill.md"><code>/skill.md</code></a>,
a public price quote at <a href="/quote"><code>/quote</code></a> — and a
listing on Coinbase's Agent Bazaar (resource 21729).</p>

<h2>Step 1 — The request (free)</h2>
<pre>curl -X POST https://token-verdict-production.up.railway.app/v1/verdict \\
  -H 'content-type: application/json' \\
  -d '{"address":"0x2821d96B5FC81aEC3763340b375a846a89A3Ec1b","chain":"base"}'</pre>
<p>No account, no API key, no dashboard. The call is a token address and a
chain — nothing else.</p>

<h2>Step 2 — The 402 challenge (free)</h2>
<p>Unpaid, the service answers <strong>HTTP 402 Payment Required</strong>,
with the challenge carried in the <code>payment-required</code> response
header. Decoded, it is the price tag and the payment instructions in one
object — this is the real challenge this service returns:</p>
<pre>__CHALLENGE_JSON__</pre>
<p>Read it as: pay <strong>$0.02</strong> — 20,000 micro-units of USDC (the
asset contract above) — on <strong>Base</strong> (<code>eip155:8453</code>),
to the <code>payTo</code> wallet in the object, within 300 seconds. One
disclosed edit: the Bazaar extension's sample <em>output</em> is elided —
see step 4 for why.</p>

<h2>Step 3 — The payment ($0.02)</h2>
<p>Any x402 v2 client reads the challenge, signs an
&ldquo;exact&rdquo;-scheme payment authorization from the agent's own
wallet, and a facilitator settles it on Base. The agent then retries the
<em>identical</em> POST with the payment proof attached. There is no login
before or after: <strong>the payment is the authentication</strong>, and
the settlement receipt travels back with the response.</p>

__VERDICT_SECTION__

<p><a href="/">&larr; Token Verdict home</a> &middot; Source:
<a href="https://github.com/QuincyGB/token-verdict">github.com/QuincyGB/token-verdict</a></p>
</body>
</html>
"""


@app.get("/example", response_class=HTMLResponse)
async def example():
    challenge = html.escape(json.dumps(_example_challenge(), indent=2))
    page = EXAMPLE_HTML.replace("__CHALLENGE_JSON__", challenge)
    return page.replace("__VERDICT_SECTION__", _verdict_section())


@app.get("/", response_class=HTMLResponse)
async def landing():
    return LANDING_HTML


@app.get("/health")
async def health():
    return {"ok": True, "service": settings.service_name}


@app.get("/quote")
async def quote():
    """Free: what the paid endpoint costs and where it settles."""
    body = {
        "service": settings.service_name,
        "endpoint": "/v1/verdict",
        "price": settings.price_usdc,
        "asset": "USDC",
        "network": settings.network,
        "payTo": settings.pay_to,
        "protocol": "x402 v2 (exact scheme)",
    }
    if settings.solana_enabled:
        body["rails"] = [
            {
                "network": settings.network,
                "payTo": settings.pay_to,
                "asset": "USDC",
            },
            {
                "network": settings.solana_network,
                "payTo": settings.solana_pay_to,
                "asset": settings.solana_usdc_mint,
            },
        ]
    return body


@app.get("/.well-known/x402")
async def well_known(request: Request):
    return JSONResponse(x402_manifest(settings, _public_base(request)))


@app.get("/skill.md", response_class=PlainTextResponse)
async def skill(request: Request):
    return skill_md(settings, _public_base(request))


@app.get("/llms.txt", response_class=PlainTextResponse)
async def llms(request: Request):
    return llms_txt(settings, _public_base(request))


async def _run_verdict(req: VerdictRequest):
    """The verdict pipeline — shared by the x402 route and the
    RapidAPI route so both return identical bytes."""
    address = req.address.strip()
    if not address.startswith("0x") or len(address) != 42:
        return JSONResponse({"error": "address must be a 0x contract address"}, status_code=422)
    try:
        result = await analyze(address, req.chain)
    except TokenNotFound as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except Exception as e:  # noqa: BLE001 — never leak tracebacks to callers
        log.exception("verdict failed for %s", address)
        return JSONResponse({"error": f"analysis failed: {e}"}, status_code=502)
    return result


@app.post("/v1/verdict")
async def verdict(req: VerdictRequest):
    """Paid endpoint — unreachable until the x402 middleware verifies payment."""
    return await _run_verdict(req)


@app.post("/v1/verdict/rapidapi")
async def verdict_rapidapi(req: VerdictRequest, request: Request):
    """RapidAPI gateway route — RapidAPI has already authenticated and
    billed the consumer; the gateway proves itself with the per-API
    proxy secret (Studio → Definition → Security). NOT x402-gated:
    deliberately absent from the `routes` dict above."""
    secret = settings.rapidapi_proxy_secret
    if not secret:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    presented = request.headers.get("x-rapidapi-proxy-secret", "")
    if not hmac.compare_digest(presented, secret):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    log.info("rapidapi verdict call")
    return await _run_verdict(req)


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, reload=False)


if __name__ == "__main__":
    main()
