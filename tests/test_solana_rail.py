"""Offline tests for the Solana (SVM) payment rail.

Everything here runs without network and without real funds:

- Payment payloads are built with the official x402 SDK's *client* scheme
  from a throwaway in-test keypair (a test fixture holding nothing, never
  persisted, unrelated to any real wallet).
- The facilitator is a stub implementing the SDK facilitator protocol —
  /verify, /settle and /supported are answered locally.
- Blockhash hints are injected via requirements.extra and mint metadata is
  pre-seeded into the client scheme's cache, so no Solana RPC is touched.

What is pinned down: env gating, address validation, price parsing, the
local pre-verify checks (fee payer / mint / destination ATA / amount /
garbage), the replay guard, the dual-network 402 the resource server
builds, and end-to-end verify through the resource server with the stub
facilitator — including that a settled transaction cannot buy twice.
"""

from __future__ import annotations

import pytest
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from x402.mechanisms.svm.constants import TOKEN_PROGRAM_ADDRESS
from x402.mechanisms.svm.exact.client import ExactSvmScheme as ExactSvmClientScheme
from x402.mechanisms.svm.exact.server import ExactSvmScheme as ExactSvmServerScheme
from x402.mechanisms.svm.mint_cache import MintMetadata
from x402.mechanisms.svm.signers import KeypairSigner
from x402.mechanisms.svm.utils import derive_ata
from x402.schemas import PaymentPayload, PaymentRequirements, ResourceInfo, VerifyResponse
from x402.schemas.config import ResourceConfig
from x402.schemas.responses import SettleResponse, SupportedKind, SupportedResponse
from x402.server import x402ResourceServer

from app import solana_rail
from app.config import SOLANA_OWNER_WALLET, SOLANA_USDC_MINT, load_settings
from app.discovery import x402_manifest
from app.solana_rail import (
    SvmPaymentError,
    SvmReplayGuard,
    make_after_settle_hook,
    make_before_verify_hook,
    validate_svm_payment,
)

@pytest.fixture(autouse=True)
def _scrub_proxy_env(monkeypatch):
    # The sandbox exports proxy variables whose IPv6 form the local httpx
    # fork misparses when the solana-py client is CONSTRUCTED (no request
    # is made in these tests). Production (Railway) has no such variables.
    for var in (
        "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
        "ALL_PROXY", "all_proxy", "no_proxy", "NO_PROXY",
    ):
        monkeypatch.delenv(var, raising=False)


NETWORK = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
BASE_NETWORK = "eip155:8453"
MINT = SOLANA_USDC_MINT
PAY_TO = SOLANA_OWNER_WALLET
# ultravioletadao facilitator's Solana fee payer — public, from its /supported.
FEE_PAYER = "F742C4VfFLQ9zRQyithoj5229ZgtX2WqKCSFKgH2EThq"
# Any syntactically valid blockhash works offline: the local checks never
# consult the chain, and the payload builder accepts extra.recentBlockhash.
BLOCKHASH = "11111111111111111111111111111111"


# --- builders ---------------------------------------------------------------


def solana_requirements(**over) -> PaymentRequirements:
    fields = dict(
        scheme="exact",
        network=NETWORK,
        asset=MINT,
        amount="20000",
        pay_to=PAY_TO,
        max_timeout_seconds=300,
        extra={"feePayer": FEE_PAYER, "recentBlockhash": BLOCKHASH},
    )
    fields.update(over)
    return PaymentRequirements(**fields)


def make_payload(requirements: PaymentRequirements, payer: Keypair | None = None) -> PaymentPayload:
    """Build a real SVM exact payload with the SDK client scheme (offline)."""
    payer = payer or Keypair()  # throwaway test fixture — holds nothing
    scheme = ExactSvmClientScheme(KeypairSigner(payer))
    scheme._mint_cache[(str(requirements.network), requirements.asset)] = MintMetadata(
        decimals=6,
        token_program=Pubkey.from_string(TOKEN_PROGRAM_ADDRESS),
    )
    inner = scheme.create_payment_payload(requirements)
    return PaymentPayload(x402_version=2, payload=inner, accepted=requirements)


class StubFacilitator:
    """Facilitator protocol stub: knows Base + Solana v2 exact kinds.

    Matches the SDK's mixed protocol: get_supported is synchronous (the
    resource server calls it from initialize()), verify/settle are async.
    """

    def __init__(self):
        self.verify_calls = 0
        self.settle_calls = 0

    def get_supported(self) -> SupportedResponse:
        return SupportedResponse(
            kinds=[
                SupportedKind(x402_version=2, scheme="exact", network=BASE_NETWORK, extra={}),
                SupportedKind(
                    x402_version=2,
                    scheme="exact",
                    network=NETWORK,
                    extra={"feePayer": FEE_PAYER},
                ),
            ]
        )

    async def verify(self, payload, requirements) -> VerifyResponse:
        self.verify_calls += 1
        return VerifyResponse(is_valid=True, payer="stub-payer")

    async def settle(self, payload, requirements) -> SettleResponse:
        self.settle_calls += 1
        return SettleResponse(success=True, transaction="stub-signature", network=requirements.network)


# --- settings ----------------------------------------------------------------


def test_solana_rail_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SOLANA_RAIL_ENABLED", raising=False)
    settings = load_settings()
    assert settings.solana_enabled is False
    # Defaults still recorded (used the moment the rail is switched on).
    assert settings.solana_pay_to == SOLANA_OWNER_WALLET
    assert settings.solana_network == NETWORK
    assert settings.solana_usdc_mint == MINT


def test_solana_rail_env_enable_and_address_validation(monkeypatch):
    monkeypatch.setenv("SOLANA_RAIL_ENABLED", "1")
    settings = load_settings()
    assert settings.solana_enabled is True
    assert solana_rail.is_valid_solana_address(settings.solana_pay_to)

    monkeypatch.setenv("X402_SOLANA_PAY_TO", "not-a-solana-address")
    with pytest.raises(RuntimeError):
        load_settings()


# --- scheme: pricing + fee payer enhancement ----------------------------------


def test_server_scheme_parse_price_usdc():
    parsed = ExactSvmServerScheme().parse_price("$0.02", NETWORK)
    assert parsed.amount == "20000"
    assert parsed.asset == MINT


def test_enhance_payment_requirements_adds_fee_payer():
    req = solana_requirements(extra={})
    kind = SupportedKind(
        x402_version=2, scheme="exact", network=NETWORK, extra={"feePayer": FEE_PAYER}
    )
    enhanced = ExactSvmServerScheme().enhance_payment_requirements(req, kind, [])
    assert enhanced.extra["feePayer"] == FEE_PAYER


# --- local pre-verify validation -----------------------------------------------


def test_validate_accepts_wellformed_payment():
    req = solana_requirements()
    payload = make_payload(req)
    info = validate_svm_payment(payload, req, expected_mint=MINT)
    assert info.amount == 20000
    assert info.mint == MINT
    assert info.fee_payer == FEE_PAYER
    assert info.destination_ata == derive_ata(PAY_TO, MINT, info.token_program)


def test_validate_rejects_insufficient_amount():
    cheap_req = solana_requirements(amount="10000")
    payload = make_payload(cheap_req)
    with pytest.raises(SvmPaymentError) as e:
        validate_svm_payment(payload, solana_requirements(), expected_mint=MINT)
    assert "amount_insufficient" in e.value.reason


def test_validate_rejects_wrong_destination():
    other_wallet = str(Keypair().pubkey())
    wrong_req = solana_requirements(pay_to=other_wallet)
    payload = make_payload(wrong_req)
    with pytest.raises(SvmPaymentError) as e:
        validate_svm_payment(payload, solana_requirements(), expected_mint=MINT)
    assert "destination_mismatch" in e.value.reason


def test_validate_rejects_wrong_fee_payer():
    impostor = str(Keypair().pubkey())
    bad_req = solana_requirements(extra={"feePayer": impostor, "recentBlockhash": BLOCKHASH})
    payload = make_payload(bad_req)
    with pytest.raises(SvmPaymentError) as e:
        validate_svm_payment(payload, solana_requirements(), expected_mint=MINT)
    assert "fee_payer_mismatch" in e.value.reason


def test_validate_rejects_garbage_transaction():
    payload = PaymentPayload(
        x402_version=2,
        payload={"transaction": "!!!not-base64-a-transaction!!!"},
        accepted=solana_requirements(),
    )
    with pytest.raises(SvmPaymentError):
        validate_svm_payment(payload, solana_requirements(), expected_mint=MINT)


def test_validate_rejects_wrong_network():
    payload = make_payload(solana_requirements())
    devnet_req = solana_requirements(network="solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1")
    with pytest.raises(SvmPaymentError) as e:
        validate_svm_payment(payload, devnet_req, expected_mint=MINT)
    assert "network_mismatch" in e.value.reason


# --- replay guard + hooks --------------------------------------------------------


class _Ctx:
    def __init__(self, payload, requirements):
        self.payment_payload = payload
        self.requirements = requirements


def test_replay_guard_and_hooks():
    guard = SvmReplayGuard()
    before = make_before_verify_hook(guard, solana_network=NETWORK, usdc_mint=MINT)
    after_settle = make_after_settle_hook(guard, solana_network=NETWORK)

    req = solana_requirements()
    payload = make_payload(req)
    assert before(_Ctx(payload, req)) is None  # fresh payment passes
    after_settle(_Ctx(payload, req))  # settlement records it as spent

    result = before(_Ctx(payload, req))
    assert result is not None and result.reason == "duplicate_settlement"

    # A different payment (fresh memo nonce) is not a replay.
    other = make_payload(req)
    assert before(_Ctx(other, req)) is None

    # EVM requirements are never touched by the Solana hooks.
    evm_req = PaymentRequirements(
        scheme="exact",
        network=BASE_NETWORK,
        asset="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        amount="20000",
        pay_to="0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F",
        max_timeout_seconds=300,
        extra={},
    )
    assert before(_Ctx(payload, evm_req)) is None


# --- resource server: dual-network 402 + verify ---------------------------------


@pytest.mark.asyncio
async def test_resource_server_dual_rail_402_and_verify():
    from x402.mechanisms.evm.exact.server import ExactEvmScheme

    facilitator = StubFacilitator()
    server = x402ResourceServer(facilitator)
    server.register(BASE_NETWORK, ExactEvmScheme())
    server.register(NETWORK, ExactSvmServerScheme())

    guard = SvmReplayGuard()
    server.on_before_verify(make_before_verify_hook(guard, solana_network=NETWORK, usdc_mint=MINT))
    server.on_after_settle(make_after_settle_hook(guard, solana_network=NETWORK))

    server.initialize()

    sol_req = server.build_payment_requirements(
        ResourceConfig(scheme="exact", network=NETWORK, pay_to=PAY_TO, price="$0.02")
    )[0]
    base_req = server.build_payment_requirements(
        ResourceConfig(
            scheme="exact",
            network=BASE_NETWORK,
            pay_to="0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F",
            price="$0.02",
        )
    )[0]

    # The unpaid path: the 402 the middleware serves carries BOTH rails,
    # and the Solana entry is fully specified (mint, amount, payTo, feePayer).
    assert sol_req.asset == MINT
    assert sol_req.amount == "20000"
    assert sol_req.pay_to == PAY_TO
    assert sol_req.extra["feePayer"] == FEE_PAYER

    payment_required = await server.create_payment_required_response(
        requirements=[base_req, sol_req],
        resource=ResourceInfo(url="https://example.test/v1/verdict"),
    )
    networks = [a.network for a in payment_required.accepts]
    assert BASE_NETWORK in networks and NETWORK in networks

    # The paid path: a well-formed Solana payment verifies end to end.
    # (A real client supplies a fresh blockhash — via the 402's hint when
    # SOLANA_RPC_URL is configured server-side, else from its own RPC.
    # Offline, inject one so the client builder never touches the network.)
    sol_req.extra["recentBlockhash"] = BLOCKHASH
    payload = make_payload(sol_req)
    result = await server.verify_payment(payload, sol_req)
    assert result.verify.is_valid is True
    assert facilitator.verify_calls == 1

    settle = await server.settle_payment(payload, sol_req)
    assert settle.success is True

    # Replay: the same settled transaction must not verify a second time —
    # the before-verify hook aborts it locally (the SDK surfaces hook aborts
    # as PaymentAbortedError; the HTTP layer turns that into a rejection),
    # before the facilitator is even called.
    from x402.schemas.errors import PaymentAbortedError

    with pytest.raises(PaymentAbortedError, match="duplicate_settlement"):
        await server.verify_payment(payload, sol_req)
    assert facilitator.verify_calls == 1


# --- app wiring + manifest --------------------------------------------------------


def test_app_routes_advertise_solana_only_when_enabled(monkeypatch):
    import importlib

    import app.main as main_module

    monkeypatch.setenv("SOLANA_RAIL_ENABLED", "1")
    try:
        importlib.reload(main_module)
        # All three paid routes advertise the Solana accept when the rail
        # is enabled — the payment rail is per-route price only. (The
        # live dual-accept 402s are verified against production after
        # deploy; this file runs offline by design.)
        expected_price = {
            "POST /v1/verdict": "$0.02",
            "POST /v1/pre-trade": "$0.05",
            "POST /v1/batch-verdict": "$0.20",
        }
        for route, price in expected_price.items():
            accepts = main_module.routes[route].accepts
            by_network = {str(a.network): a for a in accepts}
            assert BASE_NETWORK in by_network and NETWORK in by_network, route
            assert by_network[NETWORK].pay_to == PAY_TO, route
            assert by_network[NETWORK].price == price, route
            assert by_network[BASE_NETWORK].price == price, route
    finally:
        monkeypatch.delenv("SOLANA_RAIL_ENABLED", raising=False)
        importlib.reload(main_module)
    for route in ("POST /v1/verdict", "POST /v1/pre-trade", "POST /v1/batch-verdict"):
        accepts = main_module.routes[route].accepts
        assert [str(a.network) for a in accepts] == [BASE_NETWORK], route


def test_manifest_other_routes_advertise_both_networks_when_enabled(monkeypatch):
    monkeypatch.setenv("SOLANA_RAIL_ENABLED", "1")
    settings = load_settings()
    manifest = x402_manifest(settings, "https://example.test")
    by_resource = {r["resource"]: r for r in manifest["resources"]}
    pre = by_resource["https://example.test/v1/pre-trade"]["accepts"]
    batch = by_resource["https://example.test/v1/batch-verdict"]["accepts"]
    assert [a["network"] for a in pre] == [BASE_NETWORK, NETWORK]
    assert [a["network"] for a in batch] == [BASE_NETWORK, NETWORK]
    assert pre[1]["amount"] == "50000" and pre[1]["asset"] == MINT and pre[1]["payTo"] == PAY_TO
    assert batch[1]["amount"] == "200000" and batch[1]["asset"] == MINT and batch[1]["payTo"] == PAY_TO

    monkeypatch.delenv("SOLANA_RAIL_ENABLED", raising=False)
    manifest = x402_manifest(load_settings(), "https://example.test")
    by_resource = {r["resource"]: r for r in manifest["resources"]}
    assert len(by_resource["https://example.test/v1/pre-trade"]["accepts"]) == 1
    assert len(by_resource["https://example.test/v1/batch-verdict"]["accepts"]) == 1


def test_manifest_advertises_both_networks_when_enabled(monkeypatch):
    monkeypatch.setenv("SOLANA_RAIL_ENABLED", "1")
    settings = load_settings()
    manifest = x402_manifest(settings, "https://example.test")
    accepts = manifest["resources"][0]["accepts"]
    assert len(accepts) == 2
    sol = accepts[1]
    assert sol["network"] == NETWORK
    assert sol["asset"] == MINT
    assert sol["payTo"] == PAY_TO
    assert sol["amount"] == "20000"
    assert manifest["networks"] == [BASE_NETWORK, NETWORK]

    monkeypatch.delenv("SOLANA_RAIL_ENABLED", raising=False)
    manifest = x402_manifest(load_settings(), "https://example.test")
    assert len(manifest["resources"][0]["accepts"]) == 1
