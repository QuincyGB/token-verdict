"""Route descriptions must fit the CDP facilitator's payload schema cap.

The 402 challenge's `resource.description` is echoed by the buyer into
the x402 v2 payment payload, and the CDP facilitator schema-validates
that payload at /verify. Its ResourceInfo.description cap is 512
characters: /v1/verdict (488) settled while /v1/pre-trade (535) and
/v1/batch-verdict (532) were rejected with "'paymentPayload' is
invalid: must match one of [x402V2PaymentPayload, x402V1PaymentPayload]"
— no Base payment could settle on those two routes from 2026-10-27
(when the Solana payment clause pushed both descriptions over the cap)
until the descriptions were trimmed on 2026-11-02.

This test pins every priced route's emitted description at <= 512
characters so a future description edit cannot silently re-break
payments.
"""

from __future__ import annotations

import app.main as main

CDP_DESCRIPTION_CAP = 512

# Appended to every route description when the Solana rail is enabled
# (production). Tests run with the rail off, so the emitted description
# lacks it — the cap applies to the production string, clause included.
SOLANA_CLAUSE = " Payable in USDC on Base or Solana — same price on either rail."


def test_route_descriptions_fit_facilitator_cap():
    for route, config in main.routes.items():
        assert config.description is not None, route
        effective = config.description
        if not effective.endswith("same price on either rail."):
            effective += SOLANA_CLAUSE
        assert len(effective) <= CDP_DESCRIPTION_CAP, (
            f"{route}: production description is {len(effective)} chars, "
            f"over the CDP facilitator's {CDP_DESCRIPTION_CAP}-char cap — "
            "payments on this route will fail payload validation"
        )
