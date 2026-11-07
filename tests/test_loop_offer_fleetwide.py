"""Loop block delivery layer (Next-20 #1, 2026-11-03): the
verdict_row_loop block attaches at the x402 middleware delivery
layer, so EVERY successful paid delivery on every priced Token
Verdict route carries it — per-call and Fleet-Pass funded alike.
Error responses and unpaid 402 challenges never carry it; the
402 amounts are unchanged. (Next-20 #5 per-wallet memory is
scoped to StallRow / RouteVerdict / ListSmith; this stall's block
is static, so a repeat delivery carries the same block.)

The per-call tests bypass the payment middleware in-process with
a passthrough (the paywall contract itself is pinned by the
unpaid test below, which runs against the real middleware)."""

import asyncio
import base64
import json
from types import SimpleNamespace

import httpx
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import app.ledger as ledger_mod
import app.main as main
from app import delivery_ledger as delivery_ledger_svc
from app.fleet_pass import LEDGER_URL

TOKEN = "0x940181a94A35A4569E4529A3CDfB74e38FD98631"
OWNER = "0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F"
VERDICT_UNITS = main.PRICE_UNITS["/v1/verdict"]
PRE_TRADE_UNITS = main.PRICE_UNITS["/v1/pre-trade"]
BATCH_UNITS = main.PRICE_UNITS["/v1/batch-verdict"]


async def _fake_analyze(address, chain):
    return {"verdict": "SOLID", "address": address, "chain": chain}


async def _fake_pre_trade(token, chain, wallet, spender, mode="full"):
    return {"decision": "PROCEED", "token": token}


async def _fake_batch(addresses, mode="full"):
    return {"results": [], "count": len(addresses)}


def _settled_client(monkeypatch):
    """TestClient whose payment middleware is a passthrough — a
    settled per-call delivery, in-process. The accuracy ledger is
    stubbed out so the suite never appends to the runtime ledger
    file."""
    monkeypatch.setattr(main, "analyze", _fake_analyze)
    monkeypatch.setattr(main, "run_pre_trade", _fake_pre_trade)
    monkeypatch.setattr(main, "run_batch", _fake_batch)
    monkeypatch.setattr(ledger_mod, "record_verdict", lambda result: True)

    # Fleet delivery-ledger recording (2026-11-07) is stubbed like
    # the accuracy ledger above; its own behavior is pinned by
    # tests/test_delivery_ledger_recording.py.
    async def _fake_record_delivery(payload):
        return {"recorded": True}

    monkeypatch.setattr(
        delivery_ledger_svc, "record_delivery", _fake_record_delivery
    )

    async def passthrough(request, call_next):
        return await call_next(request)

    monkeypatch.setattr(main, "_payment_middleware", passthrough)
    return TestClient(main.app, raise_server_exceptions=False)


def _assert_block(body):
    block = body["verdict_row_loop"]
    offers = {o["id"]: o for o in block["offers"]}
    assert offers["recheck_pack"]["price_usd"] == 1.0
    assert offers["fleet_pass"]["url"] == f"{LEDGER_URL}/v1/fleet-pass"
    # Static block only on this stall — no per-wallet section.
    assert "returning_payer" not in block
    return block


# --- (a) settled deliveries on every priced route carry the block --------

def test_per_call_verdict_delivery_carries_block(monkeypatch):
    client = _settled_client(monkeypatch)
    r = client.post("/v1/verdict", json={"address": TOKEN, "chain": "base"})
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "SOLID"  # original fields untouched
    _assert_block(r.json())


def test_per_call_pre_trade_delivery_carries_block(monkeypatch):
    client = _settled_client(monkeypatch)
    r = client.post(
        "/v1/pre-trade", json={"token_address": TOKEN, "chain": "base"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["decision"] == "PROCEED"
    _assert_block(r.json())


def test_per_call_batch_delivery_carries_block(monkeypatch):
    client = _settled_client(monkeypatch)
    r = client.post(
        "/v1/batch-verdict", json={"token_addresses": [TOKEN]}
    )
    assert r.status_code == 200, r.text
    _assert_block(r.json())


def _install_ledger(monkeypatch):
    real = httpx.AsyncClient

    def handler(request):
        if request.url.path.endswith("/debit"):
            return httpx.Response(200, json={"debit_id": "fpd_abc"})
        return httpx.Response(200, json={"ok": True})

    def factory(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real(transport=httpx.MockTransport(handler), timeout=10.0)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def test_fleet_pass_funded_delivery_carries_block(monkeypatch):
    monkeypatch.setattr(main, "analyze", _fake_analyze)
    monkeypatch.setattr(ledger_mod, "record_verdict", lambda result: True)
    _install_ledger(monkeypatch)
    client = TestClient(main.app, raise_server_exceptions=False)

    r = client.post(
        "/v1/verdict", json={"address": TOKEN, "chain": "base"},
        headers={"X-Fleet-Pass": "fpass_ok"},
    )
    assert r.status_code == 200, r.text
    _assert_block(r.json())


def test_repeat_delivery_still_static_block(monkeypatch):
    """No per-wallet memory on this stall: a second settled
    delivery from the same payer carries the identical static
    block, with no returning_payer section."""
    client = _settled_client(monkeypatch)
    first = client.post(
        "/v1/pre-trade", json={"token_address": TOKEN, "chain": "base"}
    )
    second = client.post(
        "/v1/pre-trade", json={"token_address": TOKEN, "chain": "base"}
    )
    assert first.json()["verdict_row_loop"] == second.json()["verdict_row_loop"]


# --- (c) 402 challenges and error responses carry no block ---------------

def test_unpaid_challenges_carry_no_block():
    client = TestClient(main.app, raise_server_exceptions=False)
    for path, body, units in (
        ("/v1/verdict", {"address": TOKEN, "chain": "base"}, VERDICT_UNITS),
        (
            "/v1/pre-trade",
            {"token_address": TOKEN, "chain": "base"},
            PRE_TRADE_UNITS,
        ),
        ("/v1/batch-verdict", {"token_addresses": [TOKEN]}, BATCH_UNITS),
    ):
        r = client.post(path, json=body)
        assert r.status_code == 402, (path, r.text)
        assert "verdict_row_loop" not in r.text
        challenge = json.loads(base64.b64decode(r.headers["payment-required"]))
        accept = challenge["accepts"][0]
        assert accept["amount"] == str(units), path
        assert accept["payTo"] == OWNER, path
    assert (VERDICT_UNITS, PRE_TRADE_UNITS, BATCH_UNITS) == (
        20000,
        50000,
        200000,
    )


def test_error_responses_carry_no_block(monkeypatch):
    client = _settled_client(monkeypatch)

    # Validation error on a priced route (settled passthrough):
    # pre-trade refuses a non-address token with a 422.
    r = client.post(
        "/v1/pre-trade", json={"token_address": "not-an-address"}
    )
    assert r.status_code == 422, r.text
    assert "verdict_row_loop" not in r.text

    # Malformed body: pydantic validation error, 422.
    r = client.post("/v1/batch-verdict", json={"token_addresses": []})
    assert r.status_code == 422, r.text
    assert "verdict_row_loop" not in r.text


def test_oversized_delivery_is_not_rewritten(monkeypatch):
    """The streaming guard: a JSON response advertising a body over
    the injection cap passes through untouched — no buffering, no
    block."""
    big = JSONResponse(
        {"dataset": "x"},
        headers={"content-length": str(main._LOOP_MAX_JSON_BYTES + 1)},
    )
    request = SimpleNamespace(
        method="POST",
        url=SimpleNamespace(path="/v1/verdict"),
        state=SimpleNamespace(),
    )
    out = asyncio.run(main._attach_loop_offer(request, big))
    assert out is big


def test_price_units_derived_from_settings():
    assert main._price_units(main.settings.price_usdc) == VERDICT_UNITS
    assert (
        main._price_units(main.settings.pre_trade_price_usdc)
        == PRE_TRADE_UNITS
    )
    assert (
        main._price_units(main.settings.batch_verdict_price_usdc)
        == BATCH_UNITS
    )
