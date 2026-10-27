"""Accuracy ledger recorder: appends one JSON line per served
verdict, extracts the fields the scoring pipeline needs, and never
raises — a broken diary must not break a paid call."""

import json

from app import ledger


RESULT = {
    "token": {
        "address": "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
        "symbol": "AERO",
        "name": "Aerodrome",
        "chain": "base",
    },
    "verdict": "SOLID",
    "score": 8.2,
    "signals": [],
    "take": "…",
    "market": {
        "price_usd": "0.6888",
        "liquidity_usd": 7874478.0,
        "dex": "aerodrome",
        "pair_url": "https://dexscreener.com/base/0x1234abcd",
    },
}


def test_record_appends_parseable_line(tmp_path):
    path = str(tmp_path / "ledger.jsonl")
    assert ledger.record_verdict(RESULT, path=path) is True
    assert ledger.record_verdict(RESULT, path=path) is True
    lines = open(path).read().strip().split("\n")
    assert len(lines) == 2
    row = json.loads(lines[0])
    assert row["verdict"] == "SOLID"
    assert row["score"] == 8.2
    assert row["address"] == RESULT["token"]["address"]
    assert row["price_usd_at_verdict"] == 0.6888
    assert row["liquidity_usd_at_verdict"] == 7874478.0
    assert row["pair_address"] == "0x1234abcd"
    assert row["dex"] == "aerodrome"
    assert row["source"] == "token-verdict-service"
    assert row["ts"].endswith("Z")


def test_record_tolerates_missing_market_fields(tmp_path):
    path = str(tmp_path / "ledger.jsonl")
    bare = {"token": {"address": "0x0", "chain": "base"}, "verdict": "DANGER", "score": 91}
    assert ledger.record_verdict(bare, path=path) is True
    row = json.loads(open(path).read())
    assert row["price_usd_at_verdict"] is None
    assert row["liquidity_usd_at_verdict"] is None
    assert row["pair_address"] is None


def test_record_never_raises_on_bad_path():
    # A directory as the target makes open() fail; the recorder must
    # swallow it and report False, not take the verdict down with it.
    assert ledger.record_verdict(RESULT, path="/") is False
