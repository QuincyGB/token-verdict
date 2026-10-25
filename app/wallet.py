"""Wallet and allowance legs for /v1/pre-trade.

Ported from the stall-row service (same operator, same stack): the
wallet verdict (services/verdict.py there) and the allowance audit
(services/reads.py there), reshaped into pre-trade "legs" with a shared
degradation contract:

  * Every leg returns a dict and does NOT raise for upstream trouble.
    A leg it could not evaluate comes back status "unknown" — on the
    sanctions leg in particular, a missing/empty OFAC snapshot NEVER
    becomes a fabricated "clean".
  * Leg shapes: token/wallet legs carry a 0-100 `risk_score`
    (higher = riskier) so pretrade.py can combine them; the allowance
    leg carries counts/findings and a risk level instead.

Data sources are the free, keyless ones stall-row already uses: the
bundled OFAC SDN snapshot, the Base public RPC, and Blockscout.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .chain import Blockscout, Rpc, UpstreamError
from .data.spenders import SPENDERS

_SNAPSHOT_PATH = Path(__file__).resolve().parent / "data" / "ofac_crypto_addresses.json"

WALLET_NOT_CHECKED = (
    "Not checked: the person or entity behind the address, contract "
    "source code, and any approvals beyond the allowance leg — sanctions "
    "matching is exact-address only against the bundled snapshot."
)
ALLOWANCE_NOT_CHECKED = (
    "Not checked: approvals to spenders outside the curated list, and "
    "Permit2's internal allowance map (the token allowance to Permit2 "
    "is only the first hop)."
)

UNLIMITED = 1 << 255
_ALLOWANCE_SEL = "dd62ed3e"


# --- OFAC sanctions snapshot ---------------------------------------------------

def _load_snapshot() -> dict:
    try:
        data = json.loads(_SNAPSHOT_PATH.read_text())
        addrs = {a.lower() for a in data.get("addresses", []) if isinstance(a, str)}
        return {
            "addresses": addrs,
            "source_url": data.get("source_url"),
            "snapshot_date": data.get("snapshot_date"),
            "count": len(addrs),
        }
    except (OSError, ValueError):
        return {"addresses": set(), "source_url": None, "snapshot_date": None, "count": 0}


SNAPSHOT = _load_snapshot()


def sanctions_check(address: str) -> dict:
    """Exact-match against the bundled OFAC snapshot. 'unknown' when the
    snapshot is missing/empty — never a fabricated 'clean'."""
    if not SNAPSHOT["addresses"]:
        return {
            "status": "unknown",
            "matched": None,
            "detail": "Sanctions snapshot is missing or empty in this deployment, so no match could be run.",
            "snapshot_date": SNAPSHOT["snapshot_date"],
            "snapshot_source": SNAPSHOT["source_url"],
        }
    matched = address.lower() in SNAPSHOT["addresses"]
    return {
        "status": "matched" if matched else "no_match",
        "matched": matched,
        "detail": (
            "Address appears verbatim on the OFAC SDN digital-currency list."
            if matched
            else "Address does not appear in the bundled OFAC SDN digital-currency snapshot."
        ),
        "snapshot_date": SNAPSHOT["snapshot_date"],
        "snapshot_source": SNAPSHOT["source_url"],
        "snapshot_entries": SNAPSHOT["count"],
    }


# --- address-poisoning heuristic ------------------------------------------------

def _parse_ts(item: dict) -> datetime | None:
    if item.get("timestamp"):
        try:
            return datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
        except ValueError:
            return None
    epoch = item.get("timestamp_epoch")
    if epoch:
        return datetime.fromtimestamp(epoch, tz=timezone.utc)
    return None


def _lookalike(a: str, b: str) -> bool:
    """Same first 4 and last 4 hex chars (after 0x), different address —
    the shape poisoning addresses mimic."""
    a, b = a.lower(), b.lower()
    return a != b and len(a) == 42 and len(b) == 42 and a[:6] == b[:6] and a[-4:] == b[-4:]


def poisoning_check(address: str, transfers: list[dict] | None) -> dict:
    if transfers is None:
        return {"status": "unknown", "signals": [], "detail": "Token-transfer history unavailable from the explorer."}
    counterparties = set()
    for t in transfers:
        for side in ("from", "to"):
            v = t.get(side)
            if isinstance(v, str) and v.lower() != address.lower():
                counterparties.add(v.lower())
    signals = []
    for t in transfers:
        frm = (t.get("from") or "").lower()
        raw = str(t.get("value_raw", ""))
        if not frm or frm == address.lower():
            continue
        if raw.isdigit() and int(raw) == 0:
            for cp in counterparties:
                if cp != frm and _lookalike(frm, cp):
                    signals.append(
                        {
                            "poison_sender": frm,
                            "mimics": cp,
                            "tx_hash": t.get("tx_hash"),
                            "detail": "Zero-value token transfer from an address sharing the first/last characters of a real counterparty.",
                        }
                    )
                    break
    return {
        "status": "signals_found" if signals else "none_found",
        "signals": signals[:5],
        "detail": (
            f"{len(signals)} poisoning-shaped transfer(s) in recent history."
            if signals
            else "No zero-value lookalike transfers in the recent history sampled."
        ),
    }


# --- wallet leg -------------------------------------------------------------------

def _unknown_wallet_leg(address: str, detail: str, sanctions: dict) -> dict:
    return {
        "status": "unknown",
        "address": address,
        "verdict": None,
        "risk_score": None,
        "sanctions": sanctions,
        "wallet_age_days": None,
        "first_activity": "unknown",
        "outgoing_tx_count_nonce": None,
        "poisoning": {"status": "unknown", "signals": [], "detail": "Not evaluated — the wallet leg's data sources were unavailable."},
        "reasons": [{"name": "wallet leg unavailable", "points": 0, "detail": detail}],
        "not_checked": WALLET_NOT_CHECKED,
    }


async def wallet_leg(
    address: str,
    *,
    client: httpx.AsyncClient | None = None,
    rpc: Rpc | None = None,
    scout: Blockscout | None = None,
) -> dict:
    """SAFE / CAUTION / AVOID for one Base wallet, as a pre-trade leg.

    Mirrors stall-row's /v1/wallet-verdict scoring, then inverts it to a
    risk score (100 - safety). Safety deductions: OFAC match = full;
    age <1d = 35, <7d = 20, <30d = 10; minimal outgoing activity on a
    young wallet = 10; poisoning signals in history = 25. Signals whose
    source is unavailable are excluded from scoring, never guessed.
    """
    sanctions = sanctions_check(address)
    own_client = client is None
    if client is None:
        from .config import load_settings

        s = load_settings()
        client = httpx.AsyncClient(timeout=s.fetch_timeout_s, follow_redirects=True)
        rpc = rpc or Rpc(client, s.base_rpc_url)
        scout = scout or Blockscout(client, s.blockscout_base_url)
    assert rpc is not None and scout is not None
    try:
        txs_asc = await scout.transactions(address, sort="asc", limit=1)
        first_dt = _parse_ts(txs_asc[0]) if txs_asc else None
        age_days = None
        if first_dt is not None:
            age_days = max(0, (datetime.now(timezone.utc) - first_dt).days)
        try:
            nonce = await rpc.eth_tx_count(address)
        except Exception:  # noqa: BLE001 — activity is a soft signal
            nonce = None
        transfers = await scout.token_transfers(address, limit=25)
        poison = poisoning_check(address, transfers)

        # Degradation rule: sanctions unknown AND no usable wallet data at
        # all means the leg evaluated nothing — report unknown rather than
        # a hollow SAFE built on no evidence.
        if (
            sanctions["status"] == "unknown"
            and age_days is None
            and nonce is None
            and transfers is None
        ):
            return _unknown_wallet_leg(
                address,
                "Sanctions snapshot unavailable and the explorer/RPC returned "
                "no wallet data, so the wallet leg could not be evaluated.",
                sanctions,
            )

        reasons: list[dict] = []
        safety = 100

        def deduct(name: str, points: int, detail: str) -> None:
            nonlocal safety
            reasons.append({"name": name, "points": points, "detail": detail})
            safety -= points

        if sanctions["status"] == "matched":
            deduct("OFAC SDN match", 100, sanctions["detail"])
        elif sanctions["status"] == "unknown":
            reasons.append(
                {"name": "sanctions check unavailable", "points": 0, "detail": sanctions["detail"]}
            )

        if age_days is None:
            reasons.append(
                {
                    "name": "wallet age unknown",
                    "points": 0,
                    "detail": "Explorer could not supply the first transaction, so age was not scored.",
                }
            )
        elif age_days < 1:
            deduct("brand-new wallet", 35, "First observed transaction is less than a day old.")
        elif age_days < 7:
            deduct("very young wallet", 20, f"First observed transaction is {age_days} day(s) old.")
        elif age_days < 30:
            deduct("young wallet", 10, f"First observed transaction is {age_days} day(s) old.")

        if nonce is not None and nonce <= 3 and (age_days or 0) < 30:
            deduct("minimal outgoing activity", 10, f"Only {nonce} outgoing transaction(s) ever.")

        if poison["status"] == "signals_found":
            deduct(
                "address-poisoning signals in history",
                25,
                "Zero-value transfers from lookalike addresses appear in this wallet's history — "
                "whoever interacts with it should never copy addresses from its history.",
            )

        safety = max(0, safety)
        verdict = "AVOID" if safety < 40 or sanctions["status"] == "matched" else (
            "CAUTION" if safety < 75 else "SAFE"
        )
        return {
            "status": "ok",
            "address": address,
            "verdict": verdict,
            "risk_score": 100 - safety,
            "sanctions": sanctions,
            "wallet_age_days": age_days,
            "first_activity": first_dt.isoformat() if first_dt else "unknown",
            "outgoing_tx_count_nonce": nonce,
            "poisoning": poison,
            "reasons": reasons,
            "not_checked": WALLET_NOT_CHECKED,
        }
    finally:
        if own_client:
            await client.aclose()


# --- allowance leg ------------------------------------------------------------------

def _pad_addr(address: str) -> str:
    return address.lower().removeprefix("0x").rjust(64, "0")


def _fmt_token_amount(raw: str, decimals: int | None) -> str | None:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if decimals is None:
        return str(value)
    whole = value // 10**decimals
    frac = value % 10**decimals
    if frac == 0:
        return str(whole)
    return f"{whole}.{frac:0{decimals}d}".rstrip("0")


async def allowance_leg(
    owner: str,
    spender: str | None = None,
    *,
    client: httpx.AsyncClient | None = None,
    rpc: Rpc | None = None,
    scout: Blockscout | None = None,
) -> dict:
    """ERC-20 approvals from `owner`, as a pre-trade leg.

    Without a spender: the stall-row allowance audit scoped to the
    curated spender list, over the tokens the wallet actually holds.
    With a spender: allowance(owner, spender) read for every held token
    (plus the trade token when supplied via held_or_token), including
    spenders outside the curated list — those are reported with
    spender_name null and flagged as unlisted.

    Never raises for upstream trouble: when the held-token list cannot
    be supplied, or a named-spender read cannot be completed, the leg
    degrades to status "unknown" with checks_performed said out loud.
    """
    own_client = client is None
    if client is None:
        from .config import load_settings

        s = load_settings()
        client = httpx.AsyncClient(timeout=s.fetch_timeout_s, follow_redirects=True)
        rpc = rpc or Rpc(client, s.base_rpc_url)
        scout = scout or Blockscout(client, s.blockscout_base_url)
    assert rpc is not None and scout is not None
    try:
        tokens = await scout.token_balances(owner)
        if tokens is None:
            return {
                "status": "unknown",
                "owner": owner,
                "spender": spender,
                "tokens_held_checked": "unknown",
                "spenders_checked": len(SPENDERS) if spender is None else 1,
                "allowance_checks_performed": 0,
                "active_approvals": "unknown",
                "unlimited_approvals": "unknown",
                "max_risk": "unknown",
                "summary": (
                    "Allowance leg not run: the wallet's held-token list could "
                    "not be supplied by its source, so no allowance checks were "
                    "performed — this is not a finding that the wallet has no "
                    "approvals."
                ),
                "not_checked": ALLOWANCE_NOT_CHECKED,
            }
        held = [
            t
            for t in tokens
            if t.get("address")
            and str(t.get("balance_raw", "0")).isdigit()
            and int(t["balance_raw"]) > 0
        ][:20]

        spenders = SPENDERS
        if spender is not None:
            known = [s for s in SPENDERS if s["address"].lower() == spender.lower()]
            spenders = known or [
                {"address": spender, "name": None, "note": "Spender supplied by the caller; not on the curated list."}
            ]

        findings: list[dict] = []
        checks = 0
        reads_attempted = 0
        for token in held:
            for sp in spenders:
                data = "0x" + _ALLOWANCE_SEL + _pad_addr(owner) + _pad_addr(sp["address"])
                reads_attempted += 1
                try:
                    raw = await rpc.eth_call(token["address"], data)
                    allowance = int(raw, 16)
                except (UpstreamError, ValueError):
                    continue
                checks += 1
                if allowance == 0:
                    continue
                findings.append(
                    {
                        "token_address": token["address"],
                        "token_symbol": token["symbol"],
                        "spender": sp["address"],
                        "spender_name": sp["name"],
                        "allowance": _fmt_token_amount(str(allowance), token["decimals"]),
                        "allowance_raw": str(allowance),
                        "unlimited": allowance >= UNLIMITED,
                        "risk": "high" if allowance >= UNLIMITED else "medium",
                    }
                )
        if spender is not None and reads_attempted > 0 and checks == 0:
            return {
                "status": "unknown",
                "owner": owner,
                "spender": spender,
                "tokens_held_checked": len(held),
                "spenders_checked": 1,
                "allowance_checks_performed": 0,
                "active_approvals": "unknown",
                "unlimited_approvals": "unknown",
                "max_risk": "unknown",
                "summary": (
                    "Allowance leg not completed: every allowance read for the "
                    "named spender failed upstream, so its approvals are unknown "
                    "— not zero."
                ),
                "not_checked": ALLOWANCE_NOT_CHECKED,
            }
        findings.sort(key=lambda f: (not f["unlimited"], f["token_symbol"] or ""))
        high = sum(1 for f in findings if f["unlimited"])
        max_risk = "high" if high else ("medium" if findings else "none")
        return {
            "status": "ok",
            "owner": owner,
            "spender": spender,
            "tokens_held_checked": len(held),
            "spenders_checked": len(spenders),
            "allowance_checks_performed": checks,
            "active_approvals": findings,
            "unlimited_approvals": high,
            "max_risk": max_risk,
            "summary": (
                f"{high} unlimited approval(s) and {len(findings) - high} "
                f"limited active approval(s) across {len(held)} held token(s) "
                f"and {len(spenders)} spender(s) checked."
                if findings
                else f"No active approvals found across {len(held)} held token(s) "
                f"and {len(spenders)} spender(s) checked."
            ),
            "not_checked": ALLOWANCE_NOT_CHECKED,
        }
    finally:
        if own_client:
            await client.aclose()
