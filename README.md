# Token Verdict — x402 pay-per-call token risk analysis API

Send a token contract address → get a risk verdict back.
Buyers pay **$0.02 USDC per call** on Base via the [x402 protocol](https://x402.org)
(HTTP 402). No accounts, no API keys — payment is the authentication.

## What it does

`POST /v1/verdict` with `{"address": "0x...", "chain": "base"}` returns:

- **verdict**: `SOLID` / `CAUTION` / `DANGER`
- **score**: 0–100 (higher = riskier)
- **signals**: six weighted market-structure signals — liquidity depth,
  volume churn, sell pressure, price action, trader breadth, pair age —
  each with its score and a plain-English detail
- **take**: a written analyst summary of the token's market structure
- **market**: raw data (price, liquidity, 24h volume, price change, txns)

Market data comes from the free DEXScreener API. The verdict is our own
analysis — the part no free API sells you.

## Honest limits

Market-structure analysis only. **Not checked:** contract honeypot
simulation and holder concentration (those need a paid indexer). The
verdict says what the market looks like, not what the contract does.
This is market data, not financial advice.

## Try it

```bash
# 1. Get the 402 payment challenge
curl -X POST https://<your-railway-url>/v1/verdict \
  -H 'content-type: application/json' \
  -d '{"address":"0x4200000000000000000000000000000000000042","chain":"base"}'

# 2. Pay $0.02 USDC on Base with any x402 v2 client (e.g. x402-fetch),
#    then retry with the payment header.
```

Free endpoints: `GET /health`, `GET /quote`, `GET /.well-known/x402`,
`GET /skill.md`, `GET /llms.txt`.

## Run locally

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
python -m uvicorn app.main:app --port 3000
# X402_PAY_TO defaults to the owner wallet; override per environment.
```

## Deploy (Railway)

New service → connect this repo → deploy. Zero env vars required:
`X402_PAY_TO` and `X402_NETWORK` (Base) have working defaults.
Health check path: `/health`.
