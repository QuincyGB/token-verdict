# Token Verdict — pay-per-call token risk verdicts for AI agents

**Before an AI agent spends real money on a token, it pays $0.02 to ask
Token Verdict first.** Send a token contract address → get a risk verdict
back: **SOLID / CAUTION / DANGER**, a 0–100 score, six weighted
market-structure signals explained in plain English, and a written analyst
take.

Buyers pay **$0.02 USDC per call** on Base via the
[x402 protocol](https://x402.org) (HTTP 402). No accounts, no API keys —
**payment is the authentication**: any agent with a wallet can call it
mid-decision, with no human ever visiting a website.

**Live service:** https://token-verdict-production.up.railway.app
**Price quote (free):** https://token-verdict-production.up.railway.app/quote
**Manifest:** https://token-verdict-production.up.railway.app/.well-known/x402
**Discovery:** Coinbase Agent Bazaar, resource **21729**; the 402 challenge
itself carries the Bazaar discovery extension, so directories can index the
service from its own payment response.

Built in October 2026 for Colosseum's Crypto World's Fair (Base track).
First commit: **2026-10-07** — the entire product was built inside the
competition window (Sep 14 – Oct 12, 2026).

---

## Why this exists

AI agents have started spending money on their own — trading agents,
treasury bots, payment agents — and they walk into the same traps that catch
humans: thin-liquidity tokens, churn-and-dump markets, pairs engineered to
look alive until the moment you try to sell.

The raw data to avoid those traps is free, but raw data is not a judgment.
An agent deciding in seconds doesn't need a chart; it needs a verdict it can
act on, priced so it can afford to ask **before every trade**. That is the
product: the judgment layer, sold the way agents actually buy things —
per call, in USDC, settled by the request itself.

Every agent that spends will need a risk check the way every online
business needed a credit check. Token Verdict is that check: two cents a
question.

## What a call returns

Real response shape (engine output for a live Base token, captured
2026-10-10; market data moves, so values will differ when you call):

```json
{
  "token": {"address": "0x2821d96B5FC81aEC3763340b375a846a89A3Ec1b",
            "symbol": "BASE", "name": "Base King", "chain": "base"},
  "verdict": "CAUTION",
  "score": 35.0,
  "signals": [
    {"name": "Liquidity depth", "score": 5,  "weight": 3,
     "detail": "Deep liquidity ($259,106) — the book can absorb real selling."},
    {"name": "Volume churn",    "score": 60, "weight": 2,
     "detail": "24h volume is just $0 — effectively no market."},
    {"name": "Sell pressure",   "score": 60, "weight": 2,
     "detail": "67% sells vs 33% buys — clear distribution."},
    {"name": "Price action",    "score": 10, "weight": 2,
     "detail": "24h price action (+0.6%) is unremarkable."},
    {"name": "Trader breadth",  "score": 70, "weight": 1,
     "detail": "Only 3 transactions in 24h — a handful of wallets IS the market."},
    {"name": "Pair age",        "score": 5,  "weight": 1,
     "detail": "Pair has meaningful history."}
  ],
  "take": "BASE (Base King) trades at $0.0003238 on uniswap with $259.1k in liquidity. Trading has effectively stopped — $0 of volume across 3 transactions in 24h — so this is a dormant book, not a live market, whatever the liquidity figure says. Verdict: CAUTION — ... Not checked: contract honeypot simulation and holder concentration (need a paid indexer) — this verdict covers market structure only. Chart: https://dexscreener.com/base/0xacbb...e72",
  "market": {"price_usd": "0.0003238", "liquidity_usd": 259105.9,
             "volume_24h_usd": 0.01, "price_change_24h_pct": 0.55,
             "txns_24h": {"buys": 1, "sells": 2}, "dex": "uniswap",
             "pair_url": "https://dexscreener.com/base/0xacbb...e72"}
}
```

Note what happened in this example: deep liquidity alone would have scored
the token SOLID — but nobody is actually trading it. A **dead-market
guard** in the engine floors any token with < $1,000 of 24h volume and
< 20 transactions at CAUTION, and the take says why. A dormant book is not a
healthy market, and the product refuses to call it one.

## How it works

```
agent ──POST /v1/verdict {address}──▶ 402 challenge (price, asset, network, payTo)
agent ──pays $0.02 USDC on Base (x402 exact scheme)──▶ facilitator settles
agent ──retries with payment proof──▶ verdict JSON
```

1. **Payment layer** — x402 protocol v2, `exact` scheme, Base mainnet
   (`eip155:8453`), USDC settlement through a public x402 facilitator.
   Payment replaces identity entirely: no user database, no key issuance,
   nothing to leak or revoke.
2. **Analysis layer** — on a paid call the service fetches the token's
   highest-liquidity pair from the free DEXScreener API and runs the
   six-signal engine:

   | Signal | Weight | What it catches |
   |---|---|---|
   | Liquidity depth | 3 | Thin books where one seller nukes the price |
   | Volume churn | 2 | Volume/liquidity ratios that smell of wash trading |
   | Sell pressure | 2 | Holders rushing the exit (sell share of flow) |
   | Price action | 2 | Parabolic launches and cliff dives |
   | Trader breadth | 1 | "Markets" that are ten wallets |
   | Pair age | 1 | The launch-and-dump window of brand-new pairs |

   Weighted average → 0–100 score → SOLID (< 35) / CAUTION (35–64) /
   DANGER (≥ 65), plus the dead-market guard above and a generated analyst
   take that names the specific flags.
3. **Discovery layer** — `GET /.well-known/x402` serves a machine-readable
   manifest; `GET /skill.md` and `GET /llms.txt` serve documentation written
   for agents, not humans; `GET /quote` publishes price/asset/network/
   payTo; the 402 challenge embeds the Bazaar discovery extension.

## Auditable by design

A risk score you can't check is just an opinion. Token Verdict is built so
every verdict can be re-derived:

- The engine is **deterministic** — same pair data in, same score out. No
  ML black box, no hidden state.
- Every response includes the **raw market data** the verdict was computed
  from (`market` object) and a link to the exact pair, so a caller can
  recompute the six signals independently.
- Each signal carries its own score, weight, and a plain-English reason —
  disagreements are debuggable, not matters of faith.
- The full source is public in this repo, and the engine's behaviour is
  pinned by the test suite in `tests/` (healthy market → SOLID, rug shape
  → DANGER, dead market → never SOLID).

## Honest limits

Market-structure analysis only. **Not checked:** contract honeypot
simulation and holder concentration (those need a paid indexer). The
verdict says what the market looks like, not what the contract does. This
is market data, not financial advice. The limits are stated **in the
product itself** — every take ends with the same disclosure — rather than
in fine print.

Roadmap, in order: holder-concentration and honeypot signals via a paid
indexer; per-token verdict history so repeat callers can see score drift;
Solana token coverage (the engine is chain-agnostic market analysis; the
payment rail is the work).

## Business model

- **Price:** $0.02 USDC per call — deliberately cheap enough for an agent
  to ask before *every* trade, and comfortably above the sub-cent cost of
  settling on Base. Margin is structural, not aspirational: the data
  source is free and settlement is someone else's infrastructure.
- **Distribution:** machine-first. Agent directories (Coinbase Agent
  Bazaar, x402scan-style indexers) read the manifest and the 402
  challenge; `skill.md`/`llms.txt` make the service legible to LLM agents
  choosing tools at runtime. No sales team, no dashboard, no onboarding
  funnel — the API is the storefront.
- **Market:** every autonomous agent that touches tokens is a prospective
  caller, and the call is priced as a rounding error on the trade it
  protects. Volume scales with agent adoption, not with our headcount.

## Try it

```bash
# 1. Get the 402 payment challenge (free)
curl -X POST https://token-verdict-production.up.railway.app/v1/verdict \
  -H 'content-type: application/json' \
  -d '{"address":"0x2821d96B5FC81aEC3763340b375a846a89A3Ec1b","chain":"base"}'

# 2. Pay $0.02 USDC on Base with any x402 v2 client (e.g. x402-fetch),
#    then retry the same call with the payment header.
```

Free endpoints: `GET /`, `GET /health`, `GET /quote`,
`GET /.well-known/x402`, `GET /skill.md`, `GET /llms.txt`.

## Run locally

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
python -m uvicorn app.main:app --port 3000
# X402_PAY_TO defaults to the owner wallet; override per environment.
.venv/bin/python -m pytest tests/ -q   # engine test suite (offline)
```

## Deploy (Railway)

New service → connect this repo → deploy. Zero env vars required:
`X402_PAY_TO` and `X402_NETWORK` (Base) have working defaults.
Health check path: `/health`.

## Stack

Python · FastAPI · Uvicorn · Docker · Railway · x402 protocol v2 (exact
scheme) · Base (`eip155:8453`) · USDC · DEXScreener API · Coinbase Agent
Bazaar (x402 discovery)
