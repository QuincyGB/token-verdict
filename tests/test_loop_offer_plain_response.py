"""Regression (2026-11-07): settled per-call deliveries reach the
loop layer as a PLAIN starlette Response — the x402 SDK buffers the
handler's body to settle, then rebuilds the response from those
bytes, and a plain Response carries no ``body_iterator`` (only
StreamingResponse does). The loop layer assumed a streaming shape
and raised AttributeError on every settled per-call delivery; the
catch-all handler turned that into a 502 AFTER the payment had
already settled on-chain (live repro 2026-11-07: $0.02 settled,
HTTP 502 returned). The TestClient suite never saw it because the
passthrough test harness reaches the layer in streaming shape.

Same defect class as stall-row fbf7c48 (2026-11-06); this stall's
loop layer shipped in 0437f74 and was never patched. These tests
call the layer directly with the production shape.
"""

import asyncio
import json
from types import SimpleNamespace

from fastapi.responses import JSONResponse

import app.main as main


def _request(path="/v1/verdict"):
    return SimpleNamespace(method="POST", url=SimpleNamespace(path=path))


def test_attach_handles_plain_settled_response():
    response = JSONResponse({"verdict": "SOLID", "score": 12})
    assert getattr(response, "body_iterator", None) is None  # the shape
    out = asyncio.run(main._attach_loop_offer(_request(), response))
    assert out.status_code == 200
    body = json.loads(out.body)
    assert body["verdict"] == "SOLID"  # original fields untouched
    assert "verdict_row_loop" in body


def test_attach_handles_plain_error_body_without_block():
    response = JSONResponse({"error": "no market", "code": "no_market"})
    out = asyncio.run(main._attach_loop_offer(_request(), response))
    body = json.loads(out.body)
    assert body["error"] == "no market"
    assert "verdict_row_loop" not in body
