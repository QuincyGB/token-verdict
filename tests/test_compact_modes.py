"""Compact modes for /v1/pre-trade and /v1/batch-verdict.

mode="compact" runs the identical computation as full mode and
projects the full result down to the decision-grade shape (pre-trade:
decision + worst leg + one summary per leg; batch: per-token
address/symbol/verdict/score + counts). It exists because Settled's
discovery extractor silently drops published output samples over
~1.5 KB (measured 2026-10-25: 1,453 B kept, 1,721 B dropped) and an
honest full-mode sample cannot fit under that — so each listing
publishes a real compact request/response pair instead.

Pinned here: mode correctness per route, compact == full on the
decision fields, failure-semantics parity, invalid-mode rejection,
the published compact examples byte-equal to fresh compact runs on
the fixtures, and their serialized size under the kept side of
Settled's bracket (the fleet shovel's ruler: json.dumps with
ensure_ascii=False, UTF-8 bytes, cap 1,453).
"""

import json

import jsonschema
import pytest

import app.main as main
from app import batch as batch_mod
from app import pretrade
from app.discovery import (
    BATCH_COMPACT_INPUT_EXAMPLE,
    BATCH_COMPACT_OUTPUT_EXAMPLE,
    BATCH_INPUT_SCHEMA,
    BATCH_OUTPUT_EXAMPLE,
    BATCH_OUTPUT_SCHEMA,
    PRE_TRADE_COMPACT_INPUT_EXAMPLE,
    PRE_TRADE_COMPACT_OUTPUT_EXAMPLE,
    PRE_TRADE_INPUT_SCHEMA,
    PRE_TRADE_OUTPUT_EXAMPLE,
    PRE_TRADE_OUTPUT_SCHEMA,
    VERDICT_OUTPUT_EXAMPLE,
    bazaar_batch_extension,
    bazaar_pre_trade_extension,
)
from app.verdict import TokenNotFound

WALLET = "0x1111111111111111111111111111111111111111"
SPENDER = "0x2222222222222222222222222222222222222222"
TOKEN = "0x3333333333333333333333333333333333333333"
DEAD = "0x000000000000000000000000000000000000dEaD"
SAMPLE_CAP_BYTES = 1453  # largest sample Settled was observed to keep


def _shovel_ruler(value) -> int:
    """listsmith-ci's sample-size ruler: json.dumps default separators,
    ensure_ascii=False, measured in UTF-8 bytes."""
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


# --- stubs (same seams as test_pretrade_batch.py) -----------------------------


def _token_result(score, verdict, address=TOKEN):
    return {
        "token": {"address": address, "symbol": "TKN", "name": "Token", "chain": "base"},
        "verdict": verdict,
        "score": score,
        "signals": [
            {"name": "Liquidity depth", "score": 5, "weight": 3, "detail": "deep"},
            {"name": "Trader breadth", "score": 90, "weight": 1, "detail": "riskiest signal"},
        ],
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
            "reasons": [
                {"name": "wallet leg unavailable", "points": 0, "detail": "explorer down"}
            ],
            "not_checked": "n/a",
        }
    return {
        "status": "ok",
        "address": WALLET,
        "verdict": verdict,
        "risk_score": risk,
        "sanctions": {"status": sanctions, "matched": sanctions == "matched",
                      "detail": "Address appears verbatim on the OFAC SDN list."},
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
            "summary": "allowance leg not run",
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
        "summary": "allowance summary",
        "not_checked": "n/a",
    }


def _stub_pretrade(monkeypatch, *, token=None, token_exc=None, wallet=None, allowance=None):
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


def _stub_batch_scores(monkeypatch, scores: dict, failing: set | None = None):
    failing = failing or set()

    async def fake_analyze(address, chain="base"):
        if address in failing:
            raise TokenNotFound(f"no pair for {address}")
        score = scores[address]
        verdict = "DANGER" if score >= 65 else ("CAUTION" if score >= 35 else "SOLID")
        return _token_result(score, verdict, address=address)

    monkeypatch.setattr(batch_mod, "analyze", fake_analyze)


@pytest.fixture(autouse=True)
def _clear_cache():
    batch_mod.clear_batch_cache()
    yield
    batch_mod.clear_batch_cache()


# --- pre-trade: compact mirrors full ------------------------------------------

_SCENARIOS = [
    # (kwargs for stubs, run kwargs)
    ({"token": _token_result(10, "SOLID")}, {}),
    ({"token": _token_result(80, "DANGER")}, {}),
    (
        {"token": _token_result(10, "SOLID"), "wallet": _wallet_leg(verdict="CAUTION", risk=50)},
        {"wallet_address": WALLET},
    ),
    (
        {"token": _token_result(10, "SOLID"), "wallet": _wallet_leg(status="unknown")},
        {"wallet_address": WALLET},
    ),
    (
        {
            "token": _token_result(10, "SOLID"),
            "wallet": _wallet_leg(),
            "allowance": _allowance_leg(unlimited=1),
        },
        {"wallet_address": WALLET, "spender": SPENDER},
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("stubs,run_kw", _SCENARIOS)
async def test_pretrade_compact_decision_equals_full(monkeypatch, stubs, run_kw):
    _stub_pretrade(monkeypatch, **stubs)
    full = await pretrade.run_pre_trade(TOKEN, **run_kw)
    compact = await pretrade.run_pre_trade(TOKEN, mode="compact", **run_kw)
    assert full["mode"] == "full"
    assert compact["mode"] == "compact"
    for key in ("verdict", "score", "token", "wallet", "spender", "abort_reasons", "degraded"):
        assert compact[key] == full[key], key
    # Every leg that ran appears exactly once, as a summary.
    assert [s["leg"] for s in compact["legs"]] == list(full["legs"].keys())
    for summary in compact["legs"]:
        leg = full["legs"][summary["leg"]]
        assert summary["status"] == leg["status"]
        assert summary["verdict"] == leg.get("verdict")
        assert summary["score"] == leg.get("risk_score")
        assert summary["headline"]  # never empty


@pytest.mark.asyncio
async def test_pretrade_compact_headlines_come_from_the_legs(monkeypatch):
    _stub_pretrade(
        monkeypatch,
        token=_token_result(10, "SOLID"),
        wallet=_wallet_leg(),
        allowance=_allowance_leg(unlimited=1),
    )
    out = await pretrade.run_pre_trade(
        TOKEN, wallet_address=WALLET, spender=SPENDER, mode="compact"
    )
    by_leg = {s["leg"]: s for s in out["legs"]}
    # Token leg headline = its worst-scoring signal's detail.
    assert by_leg["token"]["headline"] == "riskiest signal"
    # Allowance leg headline = its own summary; it has no verdict/score.
    assert by_leg["allowance"]["headline"] == "allowance summary"
    assert by_leg["allowance"]["verdict"] is None
    assert by_leg["allowance"]["score"] is None
    # Clean wallet: no deductions to report, statuses named plainly.
    assert by_leg["wallet"]["headline"].startswith("No risk deductions")


@pytest.mark.asyncio
async def test_pretrade_compact_unknown_leg_headline_is_its_reason(monkeypatch):
    _stub_pretrade(
        monkeypatch,
        token=_token_result(10, "SOLID"),
        wallet=_wallet_leg(status="unknown"),
    )
    out = await pretrade.run_pre_trade(TOKEN, wallet_address=WALLET, mode="compact")
    wallet = next(s for s in out["legs"] if s["leg"] == "wallet")
    assert wallet["status"] == "unknown"
    assert wallet["headline"] == "explorer down"
    assert out["verdict"] == "CAUTION"  # the degraded cap survives compacting


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stubs,run_kw,expected_worst",
    [
        ({"token": _token_result(80, "DANGER")}, {}, "token"),
        (
            {"token": _token_result(10, "SOLID"), "wallet": _wallet_leg(verdict="AVOID", risk=70)},
            {"wallet_address": WALLET},
            "wallet",
        ),
        (
            {
                "token": _token_result(10, "SOLID"),
                "wallet": _wallet_leg(verdict="AVOID", risk=100, sanctions="matched"),
            },
            {"wallet_address": WALLET},
            "wallet",
        ),
        (
            {"token": _token_result(10, "SOLID"), "wallet": _wallet_leg(verdict="CAUTION", risk=50)},
            {"wallet_address": WALLET},
            "wallet",
        ),
        ({"token": _token_result(40, "CAUTION")}, {}, "token"),
    ],
)
async def test_pretrade_compact_worst_leg(monkeypatch, stubs, run_kw, expected_worst):
    _stub_pretrade(monkeypatch, **stubs)
    out = await pretrade.run_pre_trade(TOKEN, mode="compact", **run_kw)
    assert out["worst_leg"] == expected_worst


# --- pre-trade: failure semantics parity ---------------------------------------


@pytest.mark.asyncio
async def test_pretrade_compact_all_legs_failed_is_unavailable(monkeypatch):
    _stub_pretrade(
        monkeypatch,
        token_exc=RuntimeError("dexscreener down"),
        wallet=_wallet_leg(status="unknown"),
    )
    with pytest.raises(pretrade.PreTradeUnavailable):
        await pretrade.run_pre_trade(TOKEN, wallet_address=WALLET, mode="compact")


@pytest.mark.asyncio
async def test_pretrade_compact_token_not_found_propagates(monkeypatch):
    _stub_pretrade(monkeypatch, token_exc=TokenNotFound("no pair"))
    with pytest.raises(TokenNotFound):
        await pretrade.run_pre_trade(TOKEN, mode="compact")


@pytest.mark.asyncio
async def test_pretrade_invalid_mode_rejected(monkeypatch):
    _stub_pretrade(monkeypatch, token=_token_result(10, "SOLID"))
    with pytest.raises(ValueError, match="mode"):
        await pretrade.run_pre_trade(TOKEN, mode="sideways")


# --- batch: compact mirrors full -------------------------------------------------


def _addr(n: int) -> str:
    return "0x" + format(n, "040x")


@pytest.mark.asyncio
async def test_batch_compact_decision_equals_full(monkeypatch):
    a1, a2, a3, a4 = _addr(1), _addr(2), _addr(3), _addr(4)
    scores = {a1: 10, a2: 90, a3: 50}
    _stub_batch_scores(monkeypatch, scores, failing={a4})
    full = await batch_mod.run_batch([a1, a2, a3, a4])
    batch_mod.clear_batch_cache()
    compact = await batch_mod.run_batch([a1, a2, a3, a4], mode="compact")
    assert full["mode"] == "full" and compact["mode"] == "compact"
    for key in ("chain", "requested_count", "unique_count", "evaluated_count",
                "unknown_count", "summary"):
        assert compact[key] == full[key], key
    # Same order; each compact entry is its full entry, projected.
    assert [e["address"] for e in compact["results"]] == [
        e["address"] for e in full["results"]
    ]
    for c_entry, f_entry in zip(compact["results"], full["results"]):
        assert c_entry["status"] == f_entry["status"]
        assert c_entry["verdict"] == f_entry["verdict"]
        assert c_entry["score"] == f_entry["score"]
        assert c_entry["reason"] == f_entry["reason"]
        assert c_entry["symbol"] == (f_entry["token"] or {}).get("symbol")
        assert "signals" not in c_entry and "take" not in c_entry


@pytest.mark.asyncio
async def test_batch_compact_majority_failure_is_unavailable(monkeypatch):
    a1, a2, a3 = _addr(1), _addr(2), _addr(3)
    _stub_batch_scores(monkeypatch, {a1: 10}, failing={a2, a3})
    with pytest.raises(batch_mod.BatchUnavailable):
        await batch_mod.run_batch([a1, a2, a3], mode="compact")


@pytest.mark.asyncio
async def test_batch_invalid_mode_rejected(monkeypatch):
    _stub_batch_scores(monkeypatch, {_addr(1): 10})
    with pytest.raises(ValueError, match="mode"):
        await batch_mod.run_batch([_addr(1)], mode="sideways")


# --- request models ---------------------------------------------------------------


def test_mode_defaults_to_full_on_both_models():
    assert main.PreTradeRequest(token_address=TOKEN).mode == "full"
    assert main.BatchVerdictRequest(token_addresses=[TOKEN]).mode == "full"
    assert main.PreTradeRequest(token_address=TOKEN, mode="compact").mode == "compact"


# --- discovery: schemas, listing switch, examples ---------------------------------


def test_compact_mode_in_schemas():
    assert PRE_TRADE_INPUT_SCHEMA["properties"]["mode"]["enum"] == ["full", "compact"]
    assert BATCH_INPUT_SCHEMA["properties"]["mode"]["enum"] == ["full", "compact"]
    for schema in (PRE_TRADE_OUTPUT_SCHEMA, BATCH_OUTPUT_SCHEMA):
        assert schema["properties"]["mode"]["enum"] == ["full", "compact"]


def test_all_examples_validate_against_output_schemas():
    jsonschema.validate(instance=PRE_TRADE_OUTPUT_EXAMPLE, schema=PRE_TRADE_OUTPUT_SCHEMA)
    jsonschema.validate(instance=PRE_TRADE_COMPACT_OUTPUT_EXAMPLE, schema=PRE_TRADE_OUTPUT_SCHEMA)
    jsonschema.validate(instance=BATCH_OUTPUT_EXAMPLE, schema=BATCH_OUTPUT_SCHEMA)
    jsonschema.validate(instance=BATCH_COMPACT_OUTPUT_EXAMPLE, schema=BATCH_OUTPUT_SCHEMA)
    assert PRE_TRADE_OUTPUT_EXAMPLE["mode"] == "full"
    assert BATCH_OUTPUT_EXAMPLE["mode"] == "full"


def test_extensions_publish_the_compact_pair():
    for ext, cin, cout in (
        (bazaar_pre_trade_extension(), PRE_TRADE_COMPACT_INPUT_EXAMPLE,
         PRE_TRADE_COMPACT_OUTPUT_EXAMPLE),
        (bazaar_batch_extension(), BATCH_COMPACT_INPUT_EXAMPLE,
         BATCH_COMPACT_OUTPUT_EXAMPLE),
    ):
        assert cin["mode"] == "compact"
        assert cout["mode"] == "compact"
        assert ext["info"]["input"]["body"] == cin
        assert ext["info"]["output"]["example"] == cout


def test_compact_examples_under_settled_sample_limit():
    # The point of the whole change: the published samples must sit on
    # the kept side of Settled's measured bracket, on the shovel's ruler.
    assert _shovel_ruler(PRE_TRADE_COMPACT_OUTPUT_EXAMPLE) <= SAMPLE_CAP_BYTES
    assert _shovel_ruler(BATCH_COMPACT_OUTPUT_EXAMPLE) <= SAMPLE_CAP_BYTES


def test_route_descriptions_disclose_both_modes():
    for path in ("POST /v1/pre-trade", "POST /v1/batch-verdict"):
        description = main.routes[path].description
        assert len(description) < 500
        assert "'compact'" in description


# --- byte-equality: published examples are fresh compact runs ---------------------

_FIXTURE_TOKEN_RESULT = {
    "token": VERDICT_OUTPUT_EXAMPLE["token"],
    "verdict": VERDICT_OUTPUT_EXAMPLE["verdict"],
    "score": VERDICT_OUTPUT_EXAMPLE["score"],
    "signals": VERDICT_OUTPUT_EXAMPLE["signals"],
    "take": VERDICT_OUTPUT_EXAMPLE["take"],
    "market": VERDICT_OUTPUT_EXAMPLE["market"],
}


@pytest.mark.asyncio
async def test_pretrade_compact_example_equals_fresh_run(monkeypatch):
    async def fake_analyze(address, chain="base"):
        return _FIXTURE_TOKEN_RESULT

    async def fake_wallet(address, **kw):
        return PRE_TRADE_OUTPUT_EXAMPLE["legs"]["wallet"]

    monkeypatch.setattr(pretrade, "analyze", fake_analyze)
    monkeypatch.setattr(pretrade, "wallet_leg", fake_wallet)
    inp = PRE_TRADE_COMPACT_INPUT_EXAMPLE
    result = await pretrade.run_pre_trade(
        inp["token_address"], inp["chain"], inp["wallet_address"], inp["spender"],
        mode="compact",
    )
    assert result == PRE_TRADE_COMPACT_OUTPUT_EXAMPLE


@pytest.mark.asyncio
async def test_batch_compact_example_equals_fresh_run(monkeypatch):
    async def fake_analyze(address, chain="base"):
        if address == DEAD:
            raise TokenNotFound("No DEXScreener pair found for this token on base.")
        return _FIXTURE_TOKEN_RESULT

    monkeypatch.setattr(batch_mod, "analyze", fake_analyze)
    result = await batch_mod.run_batch(
        BATCH_COMPACT_INPUT_EXAMPLE["token_addresses"], mode="compact"
    )
    assert result == BATCH_COMPACT_OUTPUT_EXAMPLE
