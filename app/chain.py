"""Minimal Base chain-read clients for the /v1/pre-trade wallet and
allowance legs: a public-RPC client and a Blockscout client.

Ported from the stall-row service (same operator, same stack), trimmed
to the methods the pre-trade legs need. No API keys exist for either
upstream: the Base public RPC (mainnet.base.org) and Blockscout's free
API are the data sources, exactly as in stall-row.

Degradation contract (identical to stall-row's): Blockscout sits behind
Cloudflare and intermittently answers with a challenge page instead of
JSON, so every Blockscout method returns None on any failure and
callers mark the affected fields "unknown" — never fabricated. RPC
failures raise UpstreamError; the pre-trade legs translate that into
degraded legs, and only a total failure of every leg becomes a 502
(the x402 middleware does not settle payment on >= 400 responses).
"""

from __future__ import annotations

import asyncio
import itertools

import httpx


class UpstreamError(Exception):
    """An upstream (RPC / explorer) failed or returned unusable data."""


def is_address(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("0x")
        and len(value) == 42
        and all(c in "0123456789abcdefABCDEF" for c in value[2:])
    )


class Rpc:
    def __init__(self, client: httpx.AsyncClient, url: str):
        self._client = client
        self._url = url
        self._ids = itertools.count(1)

    async def call(self, method: str, params: list) -> object:
        """One JSON-RPC call, with ONE retry on transient failures
        (transport errors, HTTP 429/5xx — the public Base RPC hiccups
        intermittently). A JSON-RPC error object or a malformed body is
        deterministic and is NOT retried. A JSON null result is a
        definitive answer and is returned as None, never raised."""
        payload = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": method,
            "params": params,
        }
        last_error: UpstreamError | None = None
        for attempt in range(2):
            if attempt:
                await asyncio.sleep(0.25)
            try:
                resp = await self._client.post(self._url, json=payload)
            except httpx.HTTPError as e:
                last_error = UpstreamError(f"RPC {method}: transport error: {e}")
                continue
            if resp.status_code != 200:
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_error = UpstreamError(f"RPC {method}: HTTP {resp.status_code}")
                    continue
                raise UpstreamError(f"RPC {method}: HTTP {resp.status_code}")
            try:
                body = resp.json()
            except ValueError as e:
                raise UpstreamError(f"RPC {method}: non-JSON response") from e
            if not isinstance(body, dict) or "error" in body:
                err = body.get("error") if isinstance(body, dict) else body
                raise UpstreamError(f"RPC {method}: error: {err}")
            if "result" not in body:
                raise UpstreamError(f"RPC {method}: missing result")
            return body["result"]
        assert last_error is not None
        raise last_error

    async def eth_tx_count(self, address: str) -> int:
        return int(await self.call("eth_getTransactionCount", [address, "latest"]), 16)

    async def eth_call(self, to: str, data: str) -> str:
        result = await self.call("eth_call", [{"to": to, "data": data}, "latest"])
        if not isinstance(result, str):
            raise UpstreamError("RPC eth_call: unexpected shape")
        return result


_UA = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


class Blockscout:
    def __init__(self, client: httpx.AsyncClient, base_url: str):
        self._client = client
        self._base = base_url.rstrip("/")

    async def _get_json(self, url: str, params: dict | None = None):
        try:
            resp = await self._client.get(url, params=params, headers=_UA)
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None  # Cloudflare challenge page or other non-JSON

    async def _v2(self, path: str, params: dict | None = None):
        return await self._get_json(f"{self._base}/api/v2{path}", params)

    async def _legacy(self, module: str, action: str, **params):
        body = await self._get_json(
            f"{self._base}/api", {"module": module, "action": action, **params}
        )
        if not isinstance(body, dict) or body.get("status") != "1":
            return None
        return body.get("result")

    async def transactions(
        self, address: str, *, sort: str = "desc", limit: int = 10
    ) -> list[dict] | None:
        """[{hash, timestamp}] newest-first for desc, oldest-first for asc."""
        body = await self._v2(f"/addresses/{address}/transactions", {"page_size": limit})
        if isinstance(body, dict) and isinstance(body.get("items"), list):
            items = [
                {"hash": it.get("hash"), "timestamp": it.get("timestamp")}
                for it in body["items"]
                if isinstance(it, dict) and it.get("hash")
            ]
            if sort == "asc":
                items.reverse()
            return items[:limit]
        result = await self._legacy(
            "account", "txlist", address=address, page=1, offset=limit, sort=sort,
        )
        if isinstance(result, list):
            out = []
            for it in result:
                if not isinstance(it, dict) or not it.get("hash"):
                    continue
                ts = it.get("timeStamp")
                out.append(
                    {
                        "hash": it["hash"],
                        "timestamp_epoch": int(ts) if str(ts).isdigit() else None,
                    }
                )
            return out
        return None

    async def token_transfers(
        self, address: str, *, limit: int = 25
    ) -> list[dict] | None:
        """Recent ERC-20 transfers touching the address (either direction)."""
        body = await self._v2(
            f"/addresses/{address}/token-transfers", {"page_size": limit}
        )
        if isinstance(body, dict) and isinstance(body.get("items"), list):
            out = []
            for it in body["items"]:
                if not isinstance(it, dict):
                    continue
                total = it.get("total") or {}
                out.append(
                    {
                        "from": (it.get("from") or {}).get("hash"),
                        "to": (it.get("to") or {}).get("hash"),
                        "value_raw": str(total.get("value")),
                        "token_symbol": (it.get("token") or {}).get("symbol"),
                        "tx_hash": it.get("transaction_hash"),
                        "timestamp": it.get("timestamp"),
                    }
                )
            return out
        result = await self._legacy(
            "account", "tokentx", address=address, page=1, offset=limit, sort="desc"
        )
        if isinstance(result, list):
            out = []
            for it in result:
                if not isinstance(it, dict):
                    continue
                out.append(
                    {
                        "from": it.get("from"),
                        "to": it.get("to"),
                        "value_raw": str(it.get("value")),
                        "token_symbol": it.get("tokenSymbol"),
                        "tx_hash": it.get("hash"),
                        "timestamp_epoch": int(it["timeStamp"])
                        if str(it.get("timeStamp")).isdigit()
                        else None,
                    }
                )
            return out
        return None

    async def token_balances(self, address: str) -> list[dict] | None:
        """[{address, name, symbol, decimals, balance_raw}] biggest first."""
        body = await self._v2(f"/addresses/{address}/token-balances")
        if isinstance(body, list) and body:
            out = []
            for item in body:
                token = item.get("token") or {}
                try:
                    out.append(
                        {
                            "address": token.get("address_hash") or token.get("address"),
                            "name": token.get("name"),
                            "symbol": token.get("symbol"),
                            "decimals": int(token["decimals"])
                            if token.get("decimals") is not None
                            else None,
                            "balance_raw": str(item.get("value")),
                        }
                    )
                except (TypeError, ValueError):
                    continue
            if out:
                return out
        result = await self._legacy("account", "tokenlist", address=address)
        if isinstance(result, list):
            out = []
            for item in result:
                if not isinstance(item, dict):
                    continue
                try:
                    decimals = int(item.get("tokenDecimal") or 0)
                except ValueError:
                    decimals = None
                out.append(
                    {
                        "address": item.get("contractAddress"),
                        "name": item.get("tokenName"),
                        "symbol": item.get("tokenSymbol"),
                        "decimals": decimals,
                        "balance_raw": str(item.get("balance")),
                    }
                )
            return out
        return None
