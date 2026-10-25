"""Offline tests for the pre-trade wallet/allowance legs (app/wallet.py).

One MockTransport plays every upstream (Base RPC + Blockscout v2),
mirroring the stall-row fixtures the legs were ported from. The suite
runs with zero network.
"""

import json

import httpx
import pytest

from app import wallet
from app.chain import Blockscout, Rpc

ADDR = "0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F"
OTHER = "0x1111111111111111111111111111111111111111"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
PERMIT2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3"
TX_HASH = "0x" + "ab" * 32

CHALLENGE_HTML = "<!DOCTYPE html><html><head><title>Just a moment...</title></head></html>"

RPC_URL = "https://mainnet.base.org"
SCOUT_URL = "https://base.blockscout.com"


def _handler(*, blockscout_down: bool = False, rpc_down: bool = False):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        host = request.url.host
        if host == "mainnet.base.org":
            if rpc_down:
                return httpx.Response(503, text="rpc down")
            body = json.loads(request.content)
            method, params = body["method"], body.get("params", [])
            if method == "eth_getTransactionCount":
                result = hex(137)
            elif method == "eth_call":
                data = params[0]["data"]
                spender = "0x" + data[-40:]
                result = hex((1 << 256) - 1) if spender.lower() == PERMIT2.lower() else hex(0)
            else:
                raise AssertionError(f"unmocked RPC method {method}")
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": body["id"], "result": result}
            )
        if host == "base.blockscout.com":
            if blockscout_down:
                return httpx.Response(
                    200, text=CHALLENGE_HTML, headers={"content-type": "text/html"}
                )
            path = request.url.path
            if "/api/v2/" in url:
                if path.endswith("/token-balances"):
                    return httpx.Response(
                        200,
                        json=[
                            {
                                "token": {
                                    "address_hash": USDC,
                                    "name": "USD Coin",
                                    "symbol": "USDC",
                                    "decimals": "6",
                                },
                                "value": "12500000",
                            }
                        ],
                    )
                if path.endswith("/token-transfers"):
                    return httpx.Response(
                        200,
                        json={
                            "items": [
                                {
                                    "from": {"hash": OTHER},
                                    "to": {"hash": ADDR},
                                    "total": {"value": "12500000"},
                                    "token": {"symbol": "USDC"},
                                    "transaction_hash": TX_HASH,
                                    "timestamp": "2026-10-20T09:41:52.000000Z",
                                }
                            ]
                        },
                    )
                if path.endswith("/transactions"):
                    return httpx.Response(
                        200,
                        json={
                            "items": [
                                {
                                    "hash": TX_HASH,
                                    "timestamp": "2025-11-02T14:03:11.000000Z",
                                }
                            ]
                        },
                    )
                return httpx.Response(404, json={"message": "not found"})
            return httpx.Response(
                200, json={"status": "0", "message": "not mocked", "result": None}
            )
        raise AssertionError(f"unmocked URL {url}")

    return handler


def _parts(**kw):
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler(**kw)))
    return client, Rpc(client, RPC_URL), Blockscout(client, SCOUT_URL)


# --- sanctions -----------------------------------------------------------------

def test_snapshot_is_bundled_and_dated():
    assert wallet.SNAPSHOT["count"] == 91
    assert wallet.SNAPSHOT["snapshot_date"] == "2026-10-21"
    assert "treasury.gov" in (wallet.SNAPSHOT["source_url"] or "")


def test_sanctions_match_listed_and_no_match_clean():
    listed = sorted(wallet.SNAPSHOT["addresses"])[0]
    out = wallet.sanctions_check(listed)
    assert out["status"] == "matched" and out["matched"] is True
    out = wallet.sanctions_check(ADDR)
    assert out["status"] == "no_match" and out["matched"] is False


def test_sanctions_unknown_when_snapshot_empty(monkeypatch):
    monkeypatch.setattr(
        wallet,
        "SNAPSHOT",
        {"addresses": set(), "source_url": None, "snapshot_date": None, "count": 0},
    )
    out = wallet.sanctions_check(ADDR)
    assert out["status"] == "unknown"
    assert out["matched"] is None  # never a fabricated "clean"


# --- wallet leg ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wallet_leg_happy_path():
    client, rpc, scout = _parts()
    async with client:
        out = await wallet.wallet_leg(ADDR, client=client, rpc=rpc, scout=scout)
    assert out["status"] == "ok"
    assert out["sanctions"]["status"] == "no_match"
    assert out["wallet_age_days"] is not None and out["wallet_age_days"] > 300
    assert out["outgoing_tx_count_nonce"] == 137
    assert out["verdict"] in ("SAFE", "CAUTION")
    assert out["risk_score"] == 100 - max(0, 100 - sum(r["points"] for r in out["reasons"]))


@pytest.mark.asyncio
async def test_wallet_leg_ofac_match_is_avoid():
    listed = sorted(wallet.SNAPSHOT["addresses"])[0]
    client, rpc, scout = _parts()
    async with client:
        out = await wallet.wallet_leg(listed, client=client, rpc=rpc, scout=scout)
    assert out["sanctions"]["status"] == "matched"
    assert out["verdict"] == "AVOID"
    assert out["risk_score"] == 100


@pytest.mark.asyncio
async def test_wallet_leg_partial_degrade_still_scores():
    """Explorer down: age/poisoning unknown, but sanctions + nonce still
    evaluate — the leg is ok, not unknown, and says what it skipped."""
    client, rpc, scout = _parts(blockscout_down=True)
    async with client:
        out = await wallet.wallet_leg(ADDR, client=client, rpc=rpc, scout=scout)
    assert out["status"] == "ok"
    assert out["wallet_age_days"] is None
    assert out["poisoning"]["status"] == "unknown"
    assert any(r["name"] == "wallet age unknown" for r in out["reasons"])


@pytest.mark.asyncio
async def test_wallet_leg_unknown_when_nothing_evaluates(monkeypatch):
    monkeypatch.setattr(
        wallet,
        "SNAPSHOT",
        {"addresses": set(), "source_url": None, "snapshot_date": None, "count": 0},
    )
    client, rpc, scout = _parts(blockscout_down=True, rpc_down=True)
    async with client:
        out = await wallet.wallet_leg(ADDR, client=client, rpc=rpc, scout=scout)
    assert out["status"] == "unknown"
    assert out["risk_score"] is None
    assert out["verdict"] is None


# --- allowance leg ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_allowance_leg_curated_finds_unlimited_permit2():
    client, rpc, scout = _parts()
    async with client:
        out = await wallet.allowance_leg(ADDR, client=client, rpc=rpc, scout=scout)
    assert out["status"] == "ok"
    assert out["unlimited_approvals"] == 1
    assert out["max_risk"] == "high"
    finding = out["active_approvals"][0]
    assert finding["unlimited"] is True
    assert finding["spender_name"].startswith("Permit2")
    assert finding["token_symbol"] == "USDC"


@pytest.mark.asyncio
async def test_allowance_leg_named_spender():
    client, rpc, scout = _parts()
    async with client:
        out = await wallet.allowance_leg(
            ADDR, PERMIT2, client=client, rpc=rpc, scout=scout
        )
    assert out["status"] == "ok"
    assert out["spenders_checked"] == 1
    assert out["unlimited_approvals"] == 1


@pytest.mark.asyncio
async def test_allowance_leg_unlisted_spender_is_flagged():
    clean_spender = "0x2222222222222222222222222222222222222222"
    client, rpc, scout = _parts()
    async with client:
        out = await wallet.allowance_leg(
            ADDR, clean_spender, client=client, rpc=rpc, scout=scout
        )
    assert out["status"] == "ok"
    assert out["active_approvals"] == []
    assert out["max_risk"] == "none"


@pytest.mark.asyncio
async def test_allowance_leg_unknown_when_explorer_down():
    client, rpc, scout = _parts(blockscout_down=True)
    async with client:
        out = await wallet.allowance_leg(ADDR, client=client, rpc=rpc, scout=scout)
    assert out["status"] == "unknown"
    assert out["active_approvals"] == "unknown"
    assert out["unlimited_approvals"] == "unknown"
    assert "not a finding" in out["summary"]
