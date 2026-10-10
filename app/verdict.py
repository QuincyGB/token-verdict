"""Token risk verdict engine.

DEXScreener market data (free, no key) -> weighted risk signals -> score
0-100 -> SOLID / CAUTION / DANGER, plus a written analysis.

Honest limits are stated in the output: we do NOT simulate sells
(honeypot check) and do NOT measure holder concentration — those need
a paid indexer. Everything we claim is computed from the data below.
"""

from __future__ import annotations

import logging
import time

import httpx

from .config import DEXSCREENER_API

log = logging.getLogger("token-verdict.verdict")

CHAIN_IDS = {"base": "base", "ethereum": "ethereum", "solana": "solana"}


class TokenNotFound(Exception):
    pass


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


async def fetch_token_data(address: str, chain: str = "base") -> dict:
    """Return the highest-liquidity DEXScreener pair for a token address."""
    chain_id = CHAIN_IDS.get(chain.lower(), chain.lower())
    async with httpx.AsyncClient(timeout=15) as client:
        # Primary: token profiles endpoint.
        r = await client.get(f"{DEXSCREENER_API}/tokens/v1/{chain_id}/{address}")
        pairs = r.json() if r.status_code == 200 else []
        # Fallback: search by address.
        if not pairs:
            r = await client.get(
                f"{DEXSCREENER_API}/latest/dex/search", params={"q": address}
            )
            data = r.json() if r.status_code == 200 else {}
            pairs = [
                p for p in (data.get("pairs") or [])
                if p.get("chainId") == chain_id
                and (p.get("baseToken") or {}).get("address", "").lower() == address.lower()
            ]
    if not pairs:
        raise TokenNotFound(f"No DEXScreener pair found for {address} on {chain_id}")
    pairs.sort(key=lambda p: _num((p.get("liquidity") or {}).get("usd")), reverse=True)
    return pairs[0]


def _signal(name: str, score: float, weight: float, detail: str) -> dict:
    return {"name": name, "score": round(score), "weight": weight, "detail": detail}


def compute_signals(pair: dict) -> list[dict]:
    liq = _num((pair.get("liquidity") or {}).get("usd"))
    vol24 = _num((pair.get("volume") or {}).get("h24"))
    txns = (pair.get("txns") or {}).get("h24") or {}
    buys, sells = int(txns.get("buys") or 0), int(txns.get("sells") or 0)
    change24 = _num((pair.get("priceChange") or {}).get("h24"))
    created_at = pair.get("pairCreatedAt")  # ms
    age_h = (time.time() * 1000 - created_at) / 3_600_000 if created_at else None

    signals = []

    # 1. Liquidity depth — thin books mean one seller can nuke the price.
    if liq < 5_000:
        s, d = 95, f"Only ${liq:,.0f} in liquidity — a single moderate sell moves the price violently."
    elif liq < 15_000:
        s, d = 75, f"Thin liquidity (${liq:,.0f}) — exits will suffer heavy slippage."
    elif liq < 50_000:
        s, d = 50, f"Modest liquidity (${liq:,.0f}) — okay for small size, risky for large."
    elif liq < 200_000:
        s, d = 25, f"Decent liquidity (${liq:,.0f})."
    else:
        s, d = 5, f"Deep liquidity (${liq:,.0f}) — the book can absorb real selling."
    signals.append(_signal("Liquidity depth", s, 3, d))

    # 2. Volume churn — absurd volume/liquidity ratios smell of wash trading.
    ratio = vol24 / liq if liq > 0 else 0
    if vol24 < 1_000:
        s, d = 60, f"24h volume is just ${vol24:,.0f} — effectively no market."
    elif ratio > 20:
        s, d = 80, f"24h volume (${vol24:,.0f}) is {ratio:.0f}x liquidity — classic wash-trading churn."
    elif ratio > 8:
        s, d = 55, f"24h volume is {ratio:.1f}x liquidity — unusually hot, trade carefully."
    elif ratio > 3:
        s, d = 30, f"24h volume is {ratio:.1f}x liquidity — active but plausible."
    else:
        s, d = 10, f"Volume/liquidity looks organic ({ratio:.1f}x)."
    signals.append(_signal("Volume churn", s, 2, d))

    # 3. Sell pressure — who is actually exiting.
    total = buys + sells
    sell_share = sells / total if total else 0.5
    if total == 0:
        s, d = 50, "No recorded buys or sells in 24h — no price discovery happening."
    elif sell_share > 0.75:
        s, d = 85, f"{sell_share:.0%} of 24h transactions are sells — holders are rushing the exit."
    elif sell_share > 0.65:
        s, d = 60, f"{sell_share:.0%} sells vs {1-sell_share:.0%} buys — clear distribution."
    elif sell_share > 0.55:
        s, d = 35, f"Slight sell lean ({sell_share:.0%}) — worth watching."
    else:
        s, d = 10, f"Buy/sell flow is balanced ({1-sell_share:.0%} buys)."
    signals.append(_signal("Sell pressure", s, 2, d))

    # 4. Price action — parabolic moves and cliff dives.
    if change24 <= -50:
        s, d = 70, f"Down {abs(change24):.0f}% in 24h — the chart is a cliff."
    elif change24 <= -25:
        s, d = 45, f"Down {abs(change24):.0f}% in 24h — steady bleeding."
    elif change24 >= 500:
        s, d = 75, f"Up {change24:.0f}% in 24h — parabolic moves like this usually give it all back."
    elif change24 >= 200:
        s, d = 50, f"Up {change24:.0f}% in 24h — momentum is extreme; late entries get hurt."
    else:
        s, d = 10, f"24h price action ({change24:+.1f}%) is unremarkable."
    signals.append(_signal("Price action", s, 2, d))

    # 5. Trader breadth — a market of ten wallets is not a market.
    if total < 20:
        s, d = 70, f"Only {total} transactions in 24h — a handful of wallets IS the market."
    elif total < 100:
        s, d = 40, f"{total} transactions in 24h — thin participation."
    else:
        s, d = 10, f"{total} transactions in 24h — real crowd participation."
    signals.append(_signal("Trader breadth", s, 1, d))

    # 6. Pair age — brand-new pairs with hot volume are the classic rug shape.
    if age_h is not None and age_h < 24 and vol24 > 10_000:
        s, d = 60, "Pair is under a day old with serious volume — the classic launch-and-dump window."
    elif age_h is not None and age_h < 24:
        s, d = 35, "Pair is under a day old — unproven."
    elif age_h is not None and age_h < 24 * 7:
        s, d = 20, "Pair is days old — still young."
    else:
        s, d = 5, "Pair has meaningful history."
    signals.append(_signal("Pair age", s, 1, d))

    return signals


def score_signals(signals: list[dict]) -> tuple[float, str]:
    total_w = sum(s["weight"] for s in signals)
    score = sum(s["score"] * s["weight"] for s in signals) / total_w if total_w else 0
    score = round(score, 1)
    verdict = "DANGER" if score >= 65 else ("CAUTION" if score >= 35 else "SOLID")
    return score, verdict


def market_activity(pair: dict) -> tuple[float, int]:
    """(24h volume USD, 24h transaction count) for a pair."""
    vol24 = _num((pair.get("volume") or {}).get("h24"))
    txns = (pair.get("txns") or {}).get("h24") or {}
    total = int(txns.get("buys") or 0) + int(txns.get("sells") or 0)
    return vol24, total


def apply_dead_market_guard(score: float, verdict: str, pair: dict) -> tuple[float, str]:
    """A market nobody is trading is never SOLID, however deep its liquidity.

    A pair can sit on six figures of parked liquidity with zero volume and
    three transactions a day — that is a dormant book, not a healthy market,
    and an agent told "SOLID, size normally" would be walking into a market
    with no live exit. Floor such verdicts at CAUTION (score 35).
    """
    vol24, total = market_activity(pair)
    if verdict == "SOLID" and vol24 < 1_000 and total < 20:
        return max(score, 35.0), "CAUTION"
    return score, verdict


def _fmt_usd(v: float) -> str:
    if v >= 1_000_000:
        return f"${v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v/1_000:.1f}k"
    return f"${v:,.0f}"


def write_take(symbol: str, name: str, pair: dict, signals: list[dict],
              score: float, verdict: str) -> str:
    """Templated analyst take — reads like a human wrote it, built from signals."""
    liq = _num((pair.get("liquidity") or {}).get("usd"))
    price = _num(pair.get("priceUsd"))
    dex = pair.get("dexId", "a DEX")
    url = pair.get("url", "")

    flags = [s for s in signals if s["score"] >= 50]
    calms = [s for s in signals if s["score"] < 25]

    parts = [
        f"{symbol} ({name}) trades at ${price} on {dex} with {_fmt_usd(liq)} in liquidity."
    ]
    if verdict == "SOLID":
        reassure = "; ".join(s["detail"].rstrip(".") for s in calms[:2])
        if reassure:
            parts.append(f"On the positive side: {reassure}.")
        if flags:
            parts.append(f"One thing to watch: {flags[0]['detail']}")
        parts.append(
            "Verdict: SOLID — the market structure looks healthy. "
            "Size normally, but this is market data, not financial advice."
        )
    elif verdict == "CAUTION":
        vol24, total = market_activity(pair)
        if vol24 < 1_000 and total < 20:
            parts.append(
                f"Trading has effectively stopped — {_fmt_usd(vol24)} of volume "
                f"across {total} transactions in 24h — so this is a dormant "
                "book, not a live market, whatever the liquidity figure says."
            )
        else:
            worst = "; ".join(s["detail"].rstrip(".") for s in flags[:2])
            if worst:
                parts.append(f"The flags: {worst}.")
        parts.append(
            "Verdict: CAUTION — tradable at most in small size, and the "
            "signals above are why. Don't chase green candles."
        )
    else:
        parts.append(
            "Verdict: DANGER — the market structure says the exit door is "
            "narrower than it looks. Treat any buy as a lottery ticket, "
            "not an investment."
        )
    parts.append(
        "Not checked: contract honeypot simulation and holder concentration "
        "(need a paid indexer) — this verdict covers market structure only."
    )
    if url:
        parts.append(f"Chart: {url}")
    return " ".join(parts)


async def analyze(address: str, chain: str = "base") -> dict:
    pair = await fetch_token_data(address, chain)
    base_token = pair.get("baseToken") or {}
    symbol = base_token.get("symbol", "???")
    name = base_token.get("name", "Unknown")
    signals = compute_signals(pair)
    score, verdict = score_signals(signals)
    score, verdict = apply_dead_market_guard(score, verdict, pair)
    return {
        "token": {"address": address, "symbol": symbol, "name": name, "chain": chain},
        "verdict": verdict,
        "score": score,
        "signals": signals,
        "take": write_take(symbol, name, pair, signals, score, verdict),
        "market": {
            "price_usd": pair.get("priceUsd"),
            "liquidity_usd": (pair.get("liquidity") or {}).get("usd"),
            "volume_24h_usd": (pair.get("volume") or {}).get("h24"),
            "price_change_24h_pct": (pair.get("priceChange") or {}).get("h24"),
            "txns_24h": ((pair.get("txns") or {}).get("h24") or {}),
            "dex": pair.get("dexId"),
            "pair_url": pair.get("url"),
        },
    }
