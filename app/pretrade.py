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

Modes: "full" (default) returns every leg in full. "compact" returns
the decision-grade shape — the combined verdict and score, the worst
leg, and one summary per leg (verdict, score, headline reason) —
derived from the full result by compact_pre_trade, so the two modes
run the identical computation and can never disagree. It exists
because discovery extraction (Settled's, measured 2026-10-25)
silently drops published output samples over ~1.5 KB, and an honest
full-mode response cannot fit under that.
"""

from __future__ import annotations

from .verdict import TokenNotFound, analyze
from .wallet import allowance_leg, wallet_leg

__all__ = ["PreTradeUnavailable", "compact_pre_trade", "run_pre_trade", "TokenNotFound"]


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


def _leg_headline(name: str, leg: dict) -> str:
    """The one-line WHY for a leg summary, taken from the leg itself —
    never invented: the token leg's worst-scoring signal, the wallet
    leg's sanctions hit or biggest deduction, the allowance leg's own
    summary, or an unevaluated leg's stated reason."""
    if leg.get("status") != "ok":
        if leg.get("detail"):
            return leg["detail"]
        reasons = leg.get("reasons") or []
        if reasons and reasons[0].get("detail"):
            return reasons[0]["detail"]
        if leg.get("summary"):
            return leg["summary"]
        return f"{name} leg could not be evaluated"
    if name == "token":
        signals = leg.get("signals") or []
        if signals:
            worst = max(signals, key=lambda s: s.get("score") or 0)
            if worst.get("detail"):
                return worst["detail"]
        return leg.get("take") or ""
    if name == "wallet":
        sanctions = leg.get("sanctions") or {}
        if sanctions.get("status") == "matched":
            return sanctions.get("detail") or "Wallet appears on the OFAC SDN snapshot."
        reasons = leg.get("reasons") or []
        if reasons:
            worst = max(reasons, key=lambda r: r.get("points") or 0)
            if worst.get("detail"):
                return worst["detail"]
        poisoning = leg.get("poisoning") or {}
        return (
            f"No risk deductions — sanctions {sanctions.get('status')}, "
            f"poisoning {poisoning.get('status')}."
        )
    # Allowance leg: its summary already is the headline.
    return leg.get("summary") or ""


def _leg_summary(name: str, leg: dict) -> dict:
    return {
        "leg": name,
        "status": leg.get("status"),
        "verdict": leg.get("verdict"),
        "score": leg.get("risk_score"),
        "headline": _leg_headline(name, leg),
    }


def _worst_leg(legs: dict[str, dict]) -> str | None:
    """The leg that drove the combined outcome: a hard-abort source
    first (token DANGER, wallet AVOID / sanctions match), else the
    scored leg with the highest risk (token wins ties, matching the
    combination order)."""
    token = legs.get("token") or {}
    if token.get("verdict") == "DANGER":
        return "token"
    wallet = legs.get("wallet") or {}
    sanctions = wallet.get("sanctions") or {}
    if wallet.get("verdict") == "AVOID" or sanctions.get("status") == "matched":
        return "wallet"
    worst: tuple[str, float] | None = None
    for name in ("token", "wallet"):
        leg = legs.get(name)
        if leg and leg.get("status") == "ok" and leg.get("risk_score") is not None:
            if worst is None or leg["risk_score"] > worst[1]:
                worst = (name, leg["risk_score"])
    return worst[0] if worst else None


def compact_pre_trade(result: dict) -> dict:
    """The compact shape of a full pre-trade result: the decision, the
    worst leg, and one summary per leg — enough to act on and to know
    why, without the nested full leg objects (signal arrays, market
    blocks, sanctions/poisoning detail). Pure projection of the full
    result, so compact can never disagree with full."""
    legs = result["legs"]
    # Same limits, honest pointer: the full text sends the reader to
    # per-leg not_checked fields the compact shape does not carry.
    not_checked = result["not_checked"].replace(
        "Leg-level limits are itemised in each leg's not_checked field.",
        "Leg-level limits are itemised in full mode's per-leg not_checked fields.",
    )
    return {
        "mode": "compact",
        "verdict": result["verdict"],
        "score": result["score"],
        "token": result["token"],
        "wallet": result["wallet"],
        "spender": result["spender"],
        "worst_leg": _worst_leg(legs),
        "legs": [
            _leg_summary(name, legs[name])
            for name in ("token", "wallet", "allowance")
            if name in legs
        ],
        "abort_reasons": result["abort_reasons"],
        "degraded": result["degraded"],
        "not_checked": not_checked,
    }


async def run_pre_trade(
    token_address: str,
    chain: str = "base",
    wallet_address: str | None = None,
    spender: str | None = None,
    mode: str = "full",
) -> dict:
    """Run the legs and combine them. Raises TokenNotFound when the
    token has no market pair at all (the /v1/verdict 404 contract), and
    PreTradeUnavailable when no leg delivered anything.

    mode="full" (default) returns every leg in full; mode="compact"
    returns compact_pre_trade's projection of the same result."""
    if mode not in ("full", "compact"):
        raise ValueError(f"mode must be 'full' or 'compact' (got {mode!r})")
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

    result = {
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
        "mode": "full",
    }
    if mode == "compact":
        return compact_pre_trade(result)
    return result
