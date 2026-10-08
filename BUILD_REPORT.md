# BUILD REPORT — Extractr x402 pay-per-call service

**Date:** 2026-10-06
**Status:** scaffolded + smoke-tested. NOT deployed, NOT listed. Nothing spent.
**Code:** `~/workspace/x402-service/` (app, tests, Dockerfile, README)

---

## 1. Research findings (x402 protocol, verified 2026-10-06)

- **Protocol:** x402 v2 (now under the Linux Foundation's x402 Foundation;
  Coinbase contributed it). Flow: buyer calls endpoint → server returns
  `402 Payment Required` + base64 `payment-required` header → buyer signs an
  EIP-3009 `transferWithAuthorization` → retries with payment header →
  **facilitator** verifies + settles on-chain → server returns the resource +
  `payment-response` header.
- **Seller SDK:** official Python package `x402[fastapi,evm]` (v2.25.0, current).
  FastAPI middleware: `fastapi_payment_middleware(routes, server)` with
  `PaymentOption(scheme="exact", price="$0.02", network="eip155:8453", payTo=…)`.
- **Critical gotcha (verified live):** the public `x402.org/facilitator`
  settles **testnets only** (Base Sepolia `eip155:84532`, Solana devnet…).
  **Base mainnet requires the Coinbase CDP facilitator**
  (`https://api.cdp.coinbase.com/platform/v2/x402`) with per-request Ed25519
  JWT auth from a free CDP API key. The scaffold handles both modes
  automatically (see §2).
- **Ecosystem (self-reported by Coinbase, not independently verified):**
  ~75M txns / $24M volume / 94K buyers / 22K sellers per 30d (July 2026
  snapshot). Only ~14% of indexed services actually return data when paid
  (ScoutScore) — a reliability gap a working service can exploit.
- **Discovery:** no signup anywhere. agentic.market (Coinbase-operated)
  auto-indexes a service after its **first settled payment through the CDP
  facilitator** (~6h refresh); x402scan.com takes a manual URL submission and
  reads `/.well-known/x402`; x402 Bazaar reads the discovery extension
  embedded in 402 responses.
- **Pricing landscape:** $0.0001–$1,000/call across the catalog; most utility
  endpoints sit at $0.001–$0.10.

## 2. What was built

**Service: Extractr** — `POST /v1/extract` takes a URL, returns clean readable
text + extractive summary + metadata. Chosen because: (a) every research
agent needs exactly this, constantly; (b) it works with zero API keys
(trafilatura extraction + TF-based summarization, deterministic); (c) the
"Real World Data / research" category is one of the busiest in the x402
wiki. A clearly better alternative did not emerge from research.

- `app/main.py` — FastAPI + x402 v2 middleware, paid route + free
  `/health /quote /debug /.well-known/x402 /skill.md /llms.txt`
- `app/config.py` — **payout wallet is a config value** (`X402_PAY_TO`,
  default `0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F` — owner's Base payout
  wallet, set per his 2026-10-06 decision). Network-aware facilitator
  selection: mainnet→CDP (requires keys, fails fast with instructions if
  missing), testnet→public facilitator (keyless).
- `app/cdp_auth.py` — CDP JWT auth via the **official** `cdp-sdk`
  (`generate_jwt`), per-operation fresh tokens (verify/settle/supported).
- `app/extractor.py` — httpx fetch (timeout, 2MB cap, UA) → trafilatura
  (BeautifulSoup fallback) → extractive summary. No model/API keys.
- `app/discovery.py` — Bazaar discovery extension embedded in 402s
  (input schema, output example, description) + manifest/skill/llms.txt.
- `tests/` — 4 unit tests, passing. `Dockerfile` — production image.
- Verified live: boots; unpaid `POST /v1/extract` → `402` with correct
  challenge (exact/$0.02/Base Sepolia/payTo=owner wallet); free endpoints OK.
  (Sandbox proxy quirk with bracketed IPv6 `no_proxy` worked around in
  `main.py`; not an issue on normal hosts.)

**Recommended price: $0.02 USDC/call** — undercuts typical data APIs,
well above sub-cent Base settlement cost. One-line change via
`X402_PRICE_USDC`.

## 3. Listing plan (after deploy — owner approves separately)

1. Deploy to a public HTTPS URL (Railway / Fly.io / VPS — see README).
2. **x402scan** — https://x402scan.com/resources/register → submit base URL
   (reads `/.well-known/x402`). Immediate.
3. **agentic.market** — https://agentic.market/validate → validate, then make
   **one real $0.02 self-buy** through the CDP facilitator; auto-indexed
   within ~6h. This is also the Bazaar listing trigger.
4. Optional: announce in x402/agent dev communities (e.g. x402 Discord,
   relevant subreddits) — distribution is the actual bottleneck, not tech.

## 4. Owner decisions needed (nothing proceeds without these)

1. **CDP API keys** — create free key at https://portal.cdp.coinbase.com,
   set `CDP_API_KEY_ID` / `CDP_API_KEY_SECRET`. **This is the single blocker
   for mainnet.** (Testnet works today with zero keys.)
2. **Deployment target** — Railway (easiest), Fly.io, or VPS. Needs his
   account/click; I can do Railway/Fly config once he picks.
3. **Listing** — approve after deploy: x402scan submission + one $0.02
   self-buy to trigger agentic.market/Bazaar indexing.
4. **Price** — $0.02 recommended; say the word to change.
5. **Wallet** — currently defaults to `0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F`
   (his OpenTask payout wallet, Base). Override with `X402_PAY_TO` if he
   wants earnings separated.

## 5. Honest caveats

- x402 volume is real but early; most "services" are experiments and buyer
  traffic is thin. This is a lottery ticket with good odds *relative* to
  other agent-earning channels, not a salary.
- The 14%-actually-work stat is our edge: a service that reliably returns
  data on every paid call stands out.
- CDP had a `/verify` regression mid-2026 (issue #2832); if mainnet paid
  calls fail at verify despite correct setup, check x402-foundation/x402
  issues before debugging our code.
- Costs to run: ~$0–5/mo hosting; facilitator takes no cut on Base USDC
  (Coinbase currently subsidizes); only Base gas per settlement (cents).

---

## 6. Arc port (branch `arc`, 2026-10-07)

Extractr ported to **Circle's Arc mainnet** (`eip155:5042`, USDC is the
native gas token) so it can earn on a second x402 rail — and so it qualifies
for the **Arc Microgrants** ($500 USDC × 20, deadline Oct 14, 2026;
submit a working Arc-mainnet deploy + public repo). Branch `arc` on
`QuincyGB/extractr-x402`; `master` (Base deployment) untouched.

**What changed** (all in branch `arc`, no Base behavior altered):
- `app/config.py` — added `ARC_MAINNET = "eip155:5042"` and
  `ARC_FACILITATOR_URL = "https://facilitator.ultravioletadao.xyz"`.
  Facilitator selection is now: explicit `X402_FACILITATOR_URL` wins →
  Base mainnet → CDP (needs keys) → **Arc mainnet → ultravioletadao
  (keyless)** → anything else → x402.org public facilitator (testnets).
  Added `NETWORK_LABELS` so discovery surfaces name the chain correctly.
- `app/main.py` — log line now prints facilitator URL + network; docstring
  no longer says "USDC on Base".
- `app/discovery.py` — `skill.md` names the chain from `NETWORK_LABELS`
  ("Arc (`eip155:5042`)") instead of hardcoding "Base". Manifest/quote/
  debug already read everything from settings.
- `tests/test_arc_config.py` — 6 new tests: Arc resolves to eip155:5042 +
  ultravioletadao URL with `use_cdp=False`; no CDP keys required; Base
  mainnet still fails fast without keys (regression guard); the 402
  challenge carries `network=eip155:5042`, `payTo` = owner's wallet,
  `amount="20000"` ($0.02), `asset=0x3600…0000` (Arc USDC precompile), and
  the Bazaar discovery extension intact; manifest + skill.md name Arc.

**Why ultravioletadao, not CDP or Circle's Gateway:**
- CDP's facilitator only settles Base/Polygon/Arbitrum — not Arc.
- Circle's own x402 Gateway lists Arc in payment requirements, but public
  Arc settlement was unavailable (their words: "Arc Mainnet is included in
  the x402 payment requirements, but live Arc payment settlement is
  currently unavailable through the public Circle Gateway").
- `https://facilitator.ultravioletadao.xyz/supported` (queried live
  2026-10-07) serves `eip155:5042` with the x402 **v1 + v2 `exact`** scheme,
  USDC `0x3600000000000000000000000000000000000000` (6 decimals) — verified
  end-to-end: an unpaid `POST /v1/extract` under `X402_NETWORK=eip155:5042`
  returns a well-formed 402 naming the Arc asset and the owner's wallet.
  No API keys, no signup.

**Env vars for the Arc Railway service** (nothing else needed):
- `X402_NETWORK=eip155:5042`
- `X402_PAY_TO=0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F`
  (same address works on Arc; default already this — set explicitly anyway)
- `X402_SERVICE_NAME=extractr-arc` (optional; ≤32 chars; keeps the two
  deployments distinguishable in Bazaar/indexers)
- Do **not** set `CDP_API_KEY_ID/SECRET` on the Arc service (unused there).
- Optional override: `X402_FACILITATOR_URL` if a different Arc facilitator
  is ever preferred.

**How to verify after deploy:**
1. `GET /health` → `{"ok":true,"service":"extractr-arc"}`
2. Unpaid `POST /v1/extract` → **402**; decode the `payment-required`
   header → `accepts[0].network == "eip155:5042"`,
   `accepts[0].payTo == "0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F"`,
   `accepts[0].amount == "20000"` ($0.02)
3. `GET /.well-known/x402` → `"network": "eip155:5042"`
4. `GET /debug` → `"facilitator": "https://facilitator.ultravioletadao.xyz"`
5. One real $0.02 paid call (his wallet) → triggers agentic.market
   indexing (~6h) and proves settlement works on Arc.

**Arc-specific notes:**
- Gas is paid in USDC (18-decimal native precision); keep a few cents of
  USDC on the payout wallet for settlement gas.
- Microgrant submission: https://dorahacks.io/hackathon/arc-microgrants/detail
  — deploy link + public repo (`QuincyGB/extractr-x402`, branch `arc`).
  Deadline: Oct 14, 2026.

### 6b. Open mode for PayGate upstream (branch `arc`, 2026-10-07)

PayGate wraps upstream APIs behind its own x402 paywall, so a second Arc
deployment of the same code must NOT demand its own 402 payment (PayGate's
backend would hit our 402 and the wrapped call would fail). New env-gated
open mode in `app/config.py` + `app/main.py`:

- `REQUIRE_PAYMENT` (default `"true"`). Set to `"false"` → `POST /v1/extract`
  skips the 402 challenge entirely and serves extraction directly (a log
  line records each bypass).
- `OPEN_MODE_TOKEN` (**optional** since 2026-10-07, PayGate's form takes no
  custom headers): when set, requests must carry header `X-Open-Token`
  matching it, else **403**. When unset/empty, the app starts normally,
  logs a warning (`open mode without token — upstream relies on PayGate
  paywall`), and serves `/v1/extract` with no header requirement — PayGate
  is the paywall in that setup.
- In open mode, requests must carry header `X-Open-Token` matching
  `OPEN_MODE_TOKEN` **only when the token is set**, else **403**. (Kept
  for setups where the upstream is called directly with a shared secret.)

**Env vars for the open-mode Railway service** (`extractr-arc-open`):
- `X402_NETWORK=eip155:5042`
- `X402_PAY_TO=0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F`
- `X402_SERVICE_NAME=extractr-arc-open` (≤32 chars)
- `REQUIRE_PAYMENT=false`
- `OPEN_MODE_TOKEN` — **can be left unset** for the PayGate upstream (no
  custom headers in their form). Set it only if a shared-secret header is
  wanted; never commit the value.
- Do **not** set `CDP_API_KEY_ID/SECRET` (unused on Arc).

**How to verify after deploy:**
1. `GET /health` → `{"ok":true,"service":"extractr-arc-open"}`
   (note: with `OPEN_MODE_TOKEN` set, /health also requires the header)
2. `GET /debug` → `"require_payment": false`
3. Unpaid `POST /v1/extract` **without** token set → **200** with extraction
   (no 402 involved). With `OPEN_MODE_TOKEN` set: **without** `X-Open-Token`
   → **403**; **with** it → **200**.

Tests: `tests/test_open_mode.py` (7 tests) — 200 with correct header,
403 without/wrong header (token-set case), open mode starts without token
and serves 200 with no header, default still 402.
Full suite: 19 passed.
