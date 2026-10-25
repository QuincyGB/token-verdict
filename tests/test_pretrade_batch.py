"""Offline tests for the /v1/pre-trade combiner and /v1/batch-verdict
engine. Leg fetchers / the single-token engine are stubbed — the seam
the production modules expose — so the suite runs with zero network.
HTTP wiring (402 prices, discovery) is pinned in test_new_routes.py.
"""

import pytest

from app import batch as batch_mod
from app import pretrade
from app.verdict import TokenNotFound

WALLET = "0x1111111111111111111111111111111111111111"
SPENDER = "0x2222222222222222222222222222222222222222"
TOKEN = "0x3333333333333333333333333333333333333333"


def _token_result(score, verdict, address=TOKEN):
    return {
        "token": {"address": address, "symbol": "TKN", "name": "Token", "chain": "base"},
        "verdict": verdict,
        "score": score,
        "signals": [],
        "take": "take",
        "market": {},
    }


def _wallet_leg(status="ok", verdict="SAFE", risk=5, sanctions="no_match"):
    if status != "ok":
        return {
            "status": "unknown",
            "address": WALLET,
            "verdict": None,
            "risk_score": None,
            "sanctions": {"status": "unknown", "matched": None},
            "wallet_age_days": None,
            "first_activity": "unknown",
            "outgoing_tx_count_nonce": None,
            "poisoning": {"status": "unknown", "signals": [], "detail": ""},
            "reasons": [],
            "not_checked": "n/a",
        }
    return {
        "status": "ok",
        "address": WALLET,
        "verdict": verdict,
        "risk_score": risk,
        "sanctions": {
            "status": sanctions,
            "matched": sanctions == "matched",
        },
        "wallet_age_days": 400,
        "first_activity": "2025-01-01T00:00:00+00:00",
        "outgoing_tx_count_nonce": 50,
        "poisoning": {"status": "none_found", "signals": [], "detail": ""},
        "reasons": [],
        "not_checked": "n/a",
    }


def _allowance_leg(status="ok", unlimited=0):
    if status != "ok":
        return {
            "status": "unknown",
            "owner": WALLET,
            "spender": SPENDER,
            "tokens_held_checked": "unknown",
            "spenders_checked": 1,
            "allowance_checks_performed": 0,
            "active_approvals": "unknown",
            "unlimited_approvals": "unknown",
            "max_risk": "unknown",
            "summary": "not run",
            "not_checked": "n/a",
        }
    return {
        "status": "ok",
        "owner": WALLET,
        "spender": SPENDER,
        "tokens_held_checked": 1,
        "spenders_checked": 1,
        "allowance_checks_performed": 1,
        "active_approvals": [],
        "unlimited_approvals": unlimited,
        "max_risk": "high" if unlimited else "none",
        "summary": "summary",
        "not_checked": "n/a",
    }


def _stub(monkeypatch, *, token=None, token_exc=None, wallet=None, allowance=None):
    async def fake_analyze(address, chain="base"):
        if token_exc is not None:
            raise token_exc
        return token

    async def fake_wallet(address, **kw):
        return wallet

    async def fake_allowance(owner, spender=None, **kw):
        return allowance

    monkeypatch.setattr(pretrade, "analyze", fake_analyze)
    if wallet is not None:
        monkeypatch.setattr(pretrade, "wallet_leg", fake_wallet)
    if allowance is not None:
        monkeypatch.setattr(pretrade, "allowance_leg", fake_allowance)


# --- pre-trade -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_pretrade_token_only_proceed(monkeypatch):
    _stub(monkeypatch, token=_token_result(10, "SOLID"))
    out = await pretrade.run_pre_trade(TOKEN)
    assert out["verdict"] == "PROCEED"
    assert out["score"] == 10
    assert out["legs"]["token"]["status"] == "ok"
    assert "wallet" not in out["legs"]
    assert out["degraded"] is False


@pytest.mark.asyncio
async def test_pretrade_token_danger_aborts(monkeypatch):
    _stub(monkeypatch, token=_token_result(80, "DANGER"))
    out = await pretrade.run_pre_trade(TOKEN)
    assert out["verdict"] == "ABORT"
    assert "token leg" in " ".join(out["abort_reasons"])


@pytest.mark.asyncio
async def test_pretrade_worst_leg_wins(monkeypatch):
    _stub(
        monkeypatch,
        token=_token_result(10, "SOLID"),
        wallet=_wallet_leg(verdict="CAUTION", risk=50),
    )
    out = await pretrade.run_pre_trade(TOKEN, wallet_address=WALLET)
    assert out["score"] == 50  # max, not the average (27.5)
    assert out["verdict"] == "CAUTION"


@pytest.mark.asyncio
async def test_pretrade_wallet_avoid_aborts(monkeypatch):
    _stub(
        monkeypatch,
        token=_token_result(10, "SOLID"),
        wallet=_wallet_leg(verdict="AVOID", risk=70),
    )
    out = await pretrade.run_pre_trade(TOKEN, wallet_address=WALLET)
    assert out["verdict"] == "ABORT"


@pytest.mark.asyncio
async def test_pretrade_sanctions_match_aborts(monkeypatch):
    _stub(
        monkeypatch,
        token=_token_result(10, "SOLID"),
        wallet=_wallet_leg(verdict="AVOID", risk=100, sanctions="matched"),
    )
    out = await pretrade.run_pre_trade(TOKEN, wallet_address=WALLET)
    assert out["verdict"] == "ABORT"
    assert out["score"] == 100
    assert any("OFAC" in r for r in out["abort_reasons"])


@pytest.mark.asyncio
async def test_pretrade_degraded_wallet_caps_at_caution(monkeypatch):
    _stub(
        monkeypatch,
        token=_token_result(10, "SOLID"),
        wallet=_wallet_leg(status="unknown"),
    )
    out = await pretrade.run_pre_trade(TOKEN, wallet_address=WALLET)
    assert out["verdict"] == "CAUTION"  # was PROCEED by score alone
    assert out["degraded"] is True
    assert out["legs"]["wallet"]["status"] == "unknown"


@pytest.mark.asyncio
async def test_pretrade_unlimited_allowance_caps_at_caution(monkeypatch):
    _stub(
        monkeypatch,
        token=_token_result(10, "SOLID"),
        wallet=_wallet_leg(),
        allowance=_allowance_leg(unlimited=1),
    )
    out = await pretrade.run_pre_trade(TOKEN, wallet_address=WALLET, spender=SPENDER)
    assert out["verdict"] == "CAUTION"
    assert out["legs"]["allowance"]["unlimited_approvals"] == 1


@pytest.mark.asyncio
async def test_pretrade_all_legs_failed_is_unavailable(monkeypatch):
    _stub(
        monkeypatch,
        token_exc=RuntimeError("dexscreener down"),
        wallet=_wallet_leg(status="unknown"),
    )
    with pytest.raises(pretrade.PreTradeUnavailable):
        await pretrade.run_pre_trade(TOKEN, wallet_address=WALLET)


@pytest.mark.asyncio
async def test_pretrade_token_not_found_propagates(monkeypatch):
    _stub(monkeypatch, token_exc=TokenNotFound("no pair"))
    with pytest.raises(TokenNotFound):
        await pretrade.run_pre_trade(TOKEN)


@pytest.mark.asyncio
async def test_pretrade_token_not_found_but_wallet_delivers(monkeypatch):
    """No pair for the token, but the wallet leg still has a verdict
    worth paying for — deliver it instead of the 404."""
    _stub(
        monkeypatch,
        token_exc=TokenNotFound("no pair"),
        wallet=_wallet_leg(verdict="CAUTION", risk=45),
    )
    out = await pretrade.run_pre_trade(TOKEN, wallet_address=WALLET)
    assert out["legs"]["token"]["status"] == "unknown"
    assert out["verdict"] == "CAUTION"
    assert out["score"] == 45


# --- batch -----------------------------------------------------------------------

def _addr(n: int) -> str:
    return "0x" + format(n, "040x")


@pytest.fixture(autouse=True)
def _clear_cache():
    batch_mod.clear_batch_cache()
    yield
    batch_mod.clear_batch_cache()


def _stub_batch(monkeypatch, scores: dict, failing: set | None = None):
    calls: list[str] = []
    failing = failing or set()

    async def fake_analyze(address, chain="base"):
        calls.append(address)
        if address in failing:
            raise TokenNotFound(f"no pair for {address}")
        score = scores[address]
        verdict = "DANGER" if score >= 65 else ("CAUTION" if score >= 35 else "SOLID")
        return _token_result(score, verdict, address=address)

    monkeypatch.setattr(batch_mod, "analyze", fake_analyze)
    return calls


@pytest.mark.asyncio
async def test_batch_happy_path_sorted_riskiest_first(monkeypatch):
    a1, a2, a3 = _addr(1), _addr(2), _addr(3)
    _stub_batch(monkeypatch, {a1: 10, a2: 90, a3: 50})
    out = await batch_mod.run_batch([a1, a2, a3])
    assert [r["address"] for r in out["results"]] == [a2, a3, a1]
    assert out["summary"] == {"SOLID": 1, "CAUTION": 1, "DANGER": 1, "UNKNOWN": 0}
    assert out["evaluated_count"] == 3 and out["unknown_count"] == 0


@pytest.mark.asyncio
async def test_batch_partial_failure_returns_unknown_entries(monkeypatch):
    a1, a2, a3, a4 = _addr(1), _addr(2), _addr(3), _addr(4)
    _stub_batch(monkeypatch, {a1: 10, a2: 20, a3: 30}, failing={a4})
    out = await batch_mod.run_batch([a1, a2, a3, a4])
    assert out["unknown_count"] == 1  # 1 of 4 is not > half
    last = out["results"][-1]
    assert last["status"] == "unknown" and last["verdict"] == "UNKNOWN"
    assert last["score"] is None and "no pair" in last["reason"]
    assert out["summary"]["UNKNOWN"] == 1


@pytest.mark.asyncio
async def test_batch_exactly_half_failure_still_delivers(monkeypatch):
    a1, a2, a3, a4 = _addr(1), _addr(2), _addr(3), _addr(4)
    _stub_batch(monkeypatch, {a1: 10, a2: 20}, failing={a3, a4})
    out = await batch_mod.run_batch([a1, a2, a3, a4])
    assert out["evaluated_count"] == 2 and out["unknown_count"] == 2


@pytest.mark.asyncio
async def test_batch_majority_failure_is_unavailable(monkeypatch):
    a1, a2, a3 = _addr(1), _addr(2), _addr(3)
    _stub_batch(monkeypatch, {a1: 10}, failing={a2, a3})
    with pytest.raises(batch_mod.BatchUnavailable):
        await batch_mod.run_batch([a1, a2, a3])


@pytest.mark.asyncio
async def test_batch_dedupes_and_caches(monkeypatch):
    a1, a2 = _addr(1), _addr(2)
    calls = _stub_batch(monkeypatch, {a1: 10, a2: 20})
    out = await batch_mod.run_batch([a1, a1.upper().replace("0X", "0x"), a2, a1])
    assert out["requested_count"] == 4
    assert out["unique_count"] == 2
    assert len(calls) == 2  # one fetch per unique token
    calls.clear()
    out2 = await batch_mod.run_batch([a1, a2])
    assert calls == []  # second batch inside the TTL hits the cache
    assert out2["evaluated_count"] == 2
