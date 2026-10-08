# DEPLOY PLAYBOOK — Extractr on Railway

**Status:** repo is Railway-ready, committed locally, NOT pushed. Nothing deployed, nothing spent.

## What he must do (takeover, ~10 minutes)

Railway deploys from a GitHub repo, and the repo currently exists only on this
machine. Pushing to his GitHub needs his auth, so he does the GitHub half:

1. **Create the repo on GitHub** (github.com/QuincyGB):
   - New repository → name `extractr-x402` → Public (or Private, either works) → **do NOT add a README/license** (the code's already committed) → Create.
2. **Push this code to it.** In a terminal on the machine holding the repo:
   ```
   cd ~/workspace/x402-service
   git remote add origin https://github.com/QuincyGB/extractr-x402.git
   git branch -M main
   git push -u origin main
   ```
   (He'll be asked for his GitHub credentials — a personal access token works as the password.)
3. **Create the Railway project:**
   - Go to https://railway.app → log in with GitHub (QuincyGB) → **New Project → Deploy from Repo** → select `extractr-x402`.
   - Railway auto-detects the `Dockerfile` (plus `railway.json` pins it and sets `/health` as the healthcheck). No build config needed.
4. **Add the env vars** (Project → Variables tab). All four:
   | Variable | Value |
   |---|---|
   | `X402_PAY_TO` | `0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F` (his Base payout wallet — already the code default, but set it explicitly) |
   | `X402_PRICE_USDC` | `$0.02` |
   | `X402_NETWORK` | `eip155:8453` for mainnet (real USDC) — or `eip155:84532` for a free Base Sepolia test run first |
   | `CDP_API_KEY_ID` | from https://portal.cdp.coinbase.com (free key) — **required for mainnet** |
   | `CDP_API_KEY_SECRET` | same — **required for mainnet** |
5. **Get the public URL:** Railway → Settings → Networking → **Generate Domain**. Copy the `https://…up.railway.app` URL.
6. Say **"done"** in chat with the URL.

**Costs:** Railway free tier covers this (tiny Python service). CDP API key is free. Facilitator takes no cut on Base USDC.

## What I do after he says done

1. **Verify deploy:** `GET {url}/health` → expect 200. `GET {url}/quote` → expect price `$0.02`, network `eip155:8453`, payTo = his wallet.
2. **Verify discovery:** `GET {url}/.well-known/x402` returns the manifest (this is what x402scan reads).
3. **Unpaid-call check:** `POST {url}/v1/extract` with no payment → expect `402 Payment Required` + `payment-required` header naming the CDP facilitator and his wallet.
4. **Paid end-to-end test (testnet first if he chose Sepolia):** run the paid test from the README against the live URL; confirm USDC lands in `X402_PAY_TO`.
5. **Mainnet check:** once CDP keys are set, confirm the facilitator URL in the 402 challenge is the CDP one (`https://api.cdp.coinbase.com/platform/v2/x402`).
6. **Then, only on his approval:** listing — x402scan submission + one $0.02 self-buy to trigger agentic.market/Bazaar indexing.

## If something breaks

- Deploy fails on Railway → check the deploy logs; the usual cause is a missing env var (the app fails fast with a clear message if CDP keys are absent on mainnet — switch `X402_NETWORK` to `eip155:84532` for a keyless testnet run).
- Health check fails → `railway.json` points at `/health`; confirm the service bound to `$PORT` (the Dockerfile honors `${PORT:-8000}`).
