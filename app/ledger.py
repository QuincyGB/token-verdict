"""Accuracy ledger recorder: one JSON line per verdict served.

The fleet's track record cannot be scored if verdicts are not
recorded — and before 2026-10-26 this service recorded nothing, so
every verdict it served (seeds, scout purchases, external calls) is
unrecoverable history. From now on, every verdict the paid route and
the RapidAPI route serve is appended here with the market data it was
derived from, so the published scoring pipeline (accuracy-ledger in
the x402-loop-kits repo; rules fixed in its METHODOLOGY.md before the
first scoring run) can evaluate it at +7d / +30d.

Path resolution mirrors StallRow's store discipline — measured, not
assumed: VERDICT_LEDGER_PATH wins; else a mounted /data volume; else
a local file (ephemeral across redeploys, and the health of that
arrangement is the operator's to see in the path itself).

Recording NEVER breaks a verdict: any failure is logged and dropped.
A risk product that fails its own calls to keep its diary would be
worse than a gap in the diary.
"""

from __future__ import annotations

import json
import logging
import os
import time

log = logging.getLogger("token-verdict.ledger")

VOLUME_MOUNT_PATH = "/data"
LEDGER_FILENAME = "verdict-ledger.jsonl"

_isdir = os.path.isdir
_ismount = os.path.ismount
_writable = os.access


def resolve_ledger_path() -> str:
    explicit = os.environ.get("VERDICT_LEDGER_PATH", "").strip()
    if explicit:
        return explicit
    if (
        _isdir(VOLUME_MOUNT_PATH)
        and _ismount(VOLUME_MOUNT_PATH)
        and _writable(VOLUME_MOUNT_PATH, os.W_OK)
    ):
        return os.path.join(VOLUME_MOUNT_PATH, LEDGER_FILENAME)
    return LEDGER_FILENAME


def _pair_address(pair_url: str | None) -> str | None:
    if not pair_url:
        return None
    tail = pair_url.rstrip("/").rsplit("/", 1)[-1]
    return tail or None


def record_verdict(result: dict, *, path: str | None = None) -> bool:
    """Append one ledger line for a served verdict. Returns True when
    the line landed; False (and never an exception) otherwise."""
    try:
        token = result.get("token") or {}
        market = result.get("market") or {}
        price = market.get("price_usd")
        try:
            price = float(price) if price is not None else None
        except (TypeError, ValueError):
            price = None
        liq = market.get("liquidity_usd")
        try:
            liq = float(liq) if liq is not None else None
        except (TypeError, ValueError):
            liq = None
        row = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": "token-verdict-service",
            "address": token.get("address"),
            "chain": token.get("chain"),
            "symbol": token.get("symbol"),
            "verdict": result.get("verdict"),
            "score": result.get("score"),
            "price_usd_at_verdict": price,
            "liquidity_usd_at_verdict": liq,
            "pair_address": _pair_address(market.get("pair_url")),
            "dex": market.get("dex"),
        }
        target = path or resolve_ledger_path()
        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        return True
    except Exception:  # noqa: BLE001 — the diary never breaks the call
        log.exception("accuracy ledger append failed")
        return False
