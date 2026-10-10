"""Offline tests for the Token Verdict scoring engine and discovery surfaces.

The engine is deterministic: same pair data in, same verdict out. That is
the auditability claim — anyone can re-derive a verdict from the raw market
data the API returns alongside it. These tests pin that behaviour down.
"""

import time

from app.discovery import _price_to_units, x402_manifest
from app.config import load_settings
from app.verdict import (
    apply_dead_market_guard,
    compute_signals,
    score_signals,
    write_take,
)

DAY_MS = 24 * 3600 * 1000


def pair(*, liq, vol, buys, sells, change, age_days):
    return {
        "liquidity": {"usd": liq},
        "volume": {"h24": vol},
        "txns": {"h24": {"buys": buys, "sells": sells}},
        "priceChange": {"h24": change},
        "pairCreatedAt": int(time.time() * 1000) - age_days * DAY_MS,
        "priceUsd": "1.00",
        "dexId": "uniswap",
        "url": "https://dexscreener.com/base/example",
    }


def verdict_for(p):
    signals = compute_signals(p)
    score, verdict = score_signals(signals)
    return apply_dead_market_guard(score, verdict, p), signals


def test_healthy_market_is_solid():
    p = pair(liq=2_000_000, vol=400_000, buys=900, sells=700, change=3.0, age_days=400)
    (score, verdict), _ = verdict_for(p)
    assert verdict == "SOLID"
    assert score < 35


def test_rug_shape_is_danger():
    # Brand-new pair, thin liquidity, wash churn, exit rush, vertical chart.
    p = pair(liq=8_000, vol=400_000, buys=40, sells=160, change=620.0, age_days=0)
    (score, verdict), _ = verdict_for(p)
    assert verdict == "DANGER"
    assert score >= 65


def test_dead_market_never_solid():
    # Deep parked liquidity but no trading at all: dormant, not healthy.
    p = pair(liq=259_106, vol=0.01, buys=1, sells=2, change=0.55, age_days=300)
    (score, verdict), _ = verdict_for(p)
    assert verdict == "CAUTION"
    assert score >= 35.0


def test_dead_market_guard_leaves_live_markets_alone():
    p = pair(liq=259_106, vol=50_000, buys=300, sells=200, change=1.0, age_days=300)
    (score, verdict), _ = verdict_for(p)
    assert verdict == "SOLID"


def test_score_thresholds():
    def sig(score, weight=1):
        return {"name": "x", "score": score, "weight": weight, "detail": ""}

    assert score_signals([sig(90)]) == (90.0, "DANGER")
    assert score_signals([sig(50)]) == (50.0, "CAUTION")
    assert score_signals([sig(10)]) == (10.0, "SOLID")


def test_weighted_average():
    signals = [
        {"name": "a", "score": 0, "weight": 3, "detail": ""},
        {"name": "b", "score": 100, "weight": 1, "detail": ""},
    ]
    score, verdict = score_signals(signals)
    assert score == 25.0
    assert verdict == "SOLID"


def test_six_signals_with_weights():
    p = pair(liq=100_000, vol=50_000, buys=100, sells=90, change=5.0, age_days=30)
    signals = compute_signals(p)
    assert [s["name"] for s in signals] == [
        "Liquidity depth",
        "Volume churn",
        "Sell pressure",
        "Price action",
        "Trader breadth",
        "Pair age",
    ]
    assert sum(s["weight"] for s in signals) == 11  # 3+2+2+2+1+1


def test_take_states_honest_limits():
    p = pair(liq=2_000_000, vol=400_000, buys=900, sells=700, change=3.0, age_days=400)
    (score, verdict), signals = verdict_for(p)
    take = write_take("WETH", "Wrapped Ether", p, signals, score, verdict)
    assert "Not checked: contract honeypot simulation and holder concentration" in take
    assert "SOLID" in take


def test_take_explains_dead_market():
    p = pair(liq=259_106, vol=0.01, buys=1, sells=2, change=0.55, age_days=300)
    (score, verdict), signals = verdict_for(p)
    take = write_take("BASE", "Base King", p, signals, score, verdict)
    assert "dormant" in take
    assert "CAUTION" in take


def test_manifest_price_units_and_shape():
    assert _price_to_units("$0.02") == "20000"
    assert _price_to_units("$1.50") == "1500000"
    settings = load_settings()
    manifest = x402_manifest(settings, "https://example.test")
    assert manifest["x402Version"] == 2
    assert manifest["network"] == "eip155:8453"
    resource = manifest["resources"][0]
    assert resource["price"] == "$0.02"
    assert resource["accepts"][0]["amount"] == "20000"
    assert resource["accepts"][0]["payTo"] == settings.pay_to
