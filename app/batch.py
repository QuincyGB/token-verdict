"""POST /v1/batch-verdict — screen up to 25 Base tokens in one call.

Runs the existing Token Verdict engine per token with bounded
concurrency and a short-TTL cache (repeat tokens in a batch — or across
batches inside the TTL — cost one DEXScreener fetch).

Failure honesty (the spec's contract):
  * a token whose data cannot be fetched comes back status "unknown",
    verdict UNKNOWN, with the reason — never an invented verdict;
  * if MORE than half the unique tokens cannot be fetched, raise
    BatchUnavailable: the route answers 502 and the payment does not
    settle — nobody pays for a screen that mostly failed.
"""

from __future__ import annotations

import asyncio
import time

from .chain import is_address
from .verdict import TokenNotFound, analyze

BATCH_MAX_TOKENS = 25
BATCH_CONCURRENCY = 5
BATCH_CACHE_TTL_S = 60.0


class BatchUnavailable(Exception):
    """More than half the batch could not be fetched upstream."""


_cache: dict[str, tuple[float, dict]] = {}
_semaphore = asyncio.Semaphore(BATCH_CONCURRENCY)


def clear_batch_cache() -> None:
    _cache.clear()


async def _analyze_one(address: str) -> dict:
    """One token -> a batch entry. Never raises for upstream trouble."""
    if not is_address(address):
        return {
            "address": address,
            "status": "unknown",
            "verdict": "UNKNOWN",
            "score": None,
            "token": None,
            "signals": [],
            "take": None,
            "market": None,
            "reason": "not a valid 0x contract address",
        }
    key = address.lower()
    cached = _cache.get(key)
    if cached is not None and (time.monotonic() - cached[0]) < BATCH_CACHE_TTL_S:
        return cached[1]
    try:
        async with _semaphore:
            result = await analyze(address, "base")
    except TokenNotFound as e:
        return {
            "address": address,
            "status": "unknown",
            "verdict": "UNKNOWN",
            "score": None,
            "token": None,
            "signals": [],
            "take": None,
            "market": None,
            "reason": str(e),
        }
    except Exception as e:  # noqa: BLE001 — per-token degrade, batch decides
        return {
            "address": address,
            "status": "unknown",
            "verdict": "UNKNOWN",
            "score": None,
            "token": None,
            "signals": [],
            "take": None,
            "market": None,
            "reason": f"analysis failed: {e}",
        }
    entry = {
        "address": result["token"]["address"],
        "status": "ok",
        "verdict": result["verdict"],
        "score": result["score"],
        "token": result["token"],
        "signals": result["signals"],
        "take": result["take"],
        "market": result["market"],
        "reason": None,
    }
    _cache[key] = (time.monotonic(), entry)
    return entry


async def run_batch(addresses: list[str]) -> dict:
    """Screen the unique addresses (first-seen order dedupe, case-
    insensitive). Raises BatchUnavailable when > half cannot be fetched."""
    seen: dict[str, str] = {}
    for a in addresses:
        seen.setdefault(a.lower(), a)
    unique = list(seen.values())

    entries = await asyncio.gather(*(_analyze_one(a) for a in unique))

    unknown = [e for e in entries if e["status"] != "ok"]
    if len(unknown) * 2 > len(unique):
        raise BatchUnavailable(
            f"{len(unknown)} of {len(unique)} tokens could not be fetched "
            "upstream — refusing to settle a mostly-failed screen"
        )

    known = sorted(
        (e for e in entries if e["status"] == "ok"),
        key=lambda e: e["score"],
        reverse=True,
    )
    unknowns_sorted = sorted(unknown, key=lambda e: e["address"].lower())
    counts = {"SOLID": 0, "CAUTION": 0, "DANGER": 0, "UNKNOWN": len(unknown)}
    for e in known:
        counts[e["verdict"]] = counts.get(e["verdict"], 0) + 1

    return {
        "chain": "base",
        "requested_count": len(addresses),
        "unique_count": len(unique),
        "evaluated_count": len(known),
        "unknown_count": len(unknown),
        "summary": counts,
        "results": known + unknowns_sorted,
        "not_checked": (
            "Per-token limits apply (see each result's take): market "
            "structure only — no contract honeypot simulation, no holder "
            "concentration."
        ),
    }
