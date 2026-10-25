"""POST /v1/pre-trade — one combined verdict before an agent spends.

Combines three legs (see wallet.py and verdict.py):
  token     — the existing Token Verdict engine (market-structure risk)
  wallet    — OFAC / age / activity / poisoning, when a wallet is given
  allowance — ERC-20 approvals, when wallet + spender are both given

Combination rules (the spec's contract):
  * combined risk = the WORST delivered leg's risk score. A cheap
    average would let a DANGER leg hide behind two calm ones; a gate
    is only as good as its worst signal.
  * PROCEED < 35, CAUTION 35-64, ABORT >= 65 (the token engine's bands).
  * hard aborts: token DANGER, wallet AVOID, a sanctions match, or any
    leg whose verdict is its scale's worst — combined verdict ABORT
    regardless of the numbers.
  * any leg that could not be evaluated comes back status "unknown"
    and caps the combined verdict at CAUTION — unless a hard abort
    already fired.
  * if NO leg could be evaluated, raise PreTradeUnavailable: the route
    answers 502 and the payment does not settle.
"""

from __future__ import annotations

from .verdict import TokenNotFound, analyze
from .wallet import allowance_leg, wallet_leg

__all__ = ["PreTradeUnavailable", "run_pre_trade", "TokenNotFound"]


class PreTradeUnavailable(Exception):
    """Every leg failed upstream — no truthful combined verdict exists."""


PRE_TRADE_NOT_CHECKED = (
    "Not checked: contract honeypot simulation and holder concentration "
    "(need a paid indexer), the person or entity behind the wallet, and "
    "approvals beyond the allowance leg. Leg-level limits are itemised "
    "in each leg's not_checked field."
)


def _unknown_token_leg(address: str, chain: str, detail: str) -> dict:
    return {
        "status": "unknown",
        "token": {"address": address, "chain": chain},
        "verdict": None,
        "risk_score": None,
        "signals": [],
        "take": None,
        "detail": detail,
    }


def _band(score: float) -> str:
    return "ABORT" if score >= 65 else ("CAUTION" if score >= 35 else "PROCEED")


async def run_pre_trade(
    token_address: str,
    chain: str = "base",
    wallet_address: str | None = None,
    spender: str | None = None,
) -> dict:
    """Run the legs and combine them. Raises TokenNotFound when the
    token has no market pair at all (the /v1/verdict 404 contract), and
    PreTradeUnavailable when no leg delivered anything."""
    # --- token leg -----------------------------------------------------
    token_not_found: TokenNotFound | None = None
    token_leg: dict
    try:
        result = await analyze(token_address, chain)
        token_leg = {
            "status": "ok",
            "token": result["token"],
            "verdict": result["verdict"],
            "risk_score": result["score"],
            "signals": result["signals"],
            "take": result["take"],
            "market": result["market"],
        }
    except TokenNotFound as e:
        token_not_found = e
        token_leg = _unknown_token_leg(
            token_address, chain, f"Token leg could not be evaluated: {e}"
        )
    except Exception as e:  # noqa: BLE001 — leg degrades, route decides
        token_leg = _unknown_token_leg(
            token_address, chain, f"Token leg could not be evaluated: {e}"
        )

    legs: dict[str, dict] = {"token": token_leg}

    # --- wallet + allowance legs ---------------------------------------
    if wallet_address:
        legs["wallet"] = await wallet_leg(wallet_address)
        if spender:
            legs["allowance"] = await allowance_leg(wallet_address, spender)

    # A token with no pair is a buyer error the caller must see (404),
    # exactly as /v1/verdict reports it — unless a wallet leg still
    # delivered a verdict worth paying for.
    delivered = [leg for leg in legs.values() if leg.get("status") == "ok"]
    if token_not_found is not None and not any(
        leg_name != "token" for leg_name, leg in legs.items() if leg.get("status") == "ok"
    ):
        raise token_not_found
    if not delivered:
        raise PreTradeUnavailable(
            "no pre-trade leg could be evaluated (all upstreams failed)"
        )

    # --- combine ---------------------------------------------------------
    scored = [
        leg["risk_score"]
        for leg in (legs["token"], legs.get("wallet"))
        if leg is not None and leg.get("status") == "ok" and leg.get("risk_score") is not None
    ]
    combined_score = max(scored) if scored else None

    hard_abort = False
    abort_reasons: list[str] = []
    if legs["token"].get("verdict") == "DANGER":
        hard_abort = True
        abort_reasons.append("token leg verdict is DANGER")
    wallet = legs.get("wallet")
    if wallet is not None:
        if wallet.get("verdict") == "AVOID":
            hard_abort = True
            abort_reasons.append("wallet leg verdict is AVOID")
        sanctions = wallet.get("sanctions") or {}
        if sanctions.get("status") == "matched":
            hard_abort = True
            abort_reasons.append("wallet appears on the OFAC SDN snapshot")

    degraded = any(leg.get("status") == "unknown" for leg in legs.values())

    allowance = legs.get("allowance")
    allowance_cap = False
    if allowance is not None and allowance.get("status") == "ok":
        if (allowance.get("unlimited_approvals") or 0) > 0:
            allowance_cap = True
            abort_reasons.append(
                "allowance leg found unlimited approval(s) to a known spender"
            )

    if hard_abort:
        verdict = "ABORT"
        if combined_score is None:
            combined_score = 100.0
    elif combined_score is None:
        # Only the allowance leg delivered — it has no numeric score, so
        # there is no combined verdict to sell. Treat as unavailable
        # rather than inventing a number.
        raise PreTradeUnavailable(
            "only the allowance leg delivered; no scored leg to combine"
        )
    else:
        verdict = _band(combined_score)
        if degraded and verdict == "PROCEED":
            verdict = "CAUTION"
            abort_reasons.append(
                "at least one leg could not be evaluated, capping the verdict at CAUTION"
            )
        if allowance_cap and verdict == "PROCEED":
            verdict = "CAUTION"

    return {
        "verdict": verdict,
        "score": round(float(combined_score), 1),
        "token": legs["token"].get("token")
        or {"address": token_address, "chain": chain},
        "wallet": wallet_address,
        "spender": spender,
        "legs": legs,
        "abort_reasons": abort_reasons,
        "degraded": degraded,
        "not_checked": PRE_TRADE_NOT_CHECKED,
    }
