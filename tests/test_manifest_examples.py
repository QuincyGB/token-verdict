"""Worked examples embedded in the discovery declarations.

Phase 3 fix: the x402 manifest resources used to emit the bare
*_INPUT_SCHEMA / *_OUTPUT_SCHEMA constants with no examples, even
though the same worked examples already rode in the 402 Bazaar
extensions. These tests pin that each priced resource now carries
those SAME example constants (verbatim, authored nowhere new) as the
JSON-Schema "example" keyword inside its inputSchema / outputSchema,
and that each agent-card skill carries its route's input example in
the A2A shape (a list of JSON strings, as on the stall-row card).
Declarations only — no route, price, or payment behaviour changes.
"""

import json

import jsonschema

from app.config import load_settings
from app.discovery import (
    BATCH_COMPACT_INPUT_EXAMPLE,
    BATCH_COMPACT_OUTPUT_EXAMPLE,
    BATCH_INPUT_SCHEMA,
    BATCH_OUTPUT_SCHEMA,
    PRE_TRADE_COMPACT_INPUT_EXAMPLE,
    PRE_TRADE_COMPACT_OUTPUT_EXAMPLE,
    PRE_TRADE_INPUT_SCHEMA,
    PRE_TRADE_OUTPUT_SCHEMA,
    VERDICT_INPUT_EXAMPLE,
    VERDICT_INPUT_SCHEMA,
    VERDICT_OUTPUT_EXAMPLE,
    VERDICT_OUTPUT_SCHEMA,
    agent_card,
    x402_manifest,
)

BASE = "https://example.test"

# path -> (input_schema, input_example, output_schema, output_example)
# The pre-trade / batch pairs are the COMPACT examples — the exact
# values bazaar_pre_trade_extension() / bazaar_batch_extension()
# pass into the 402 Bazaar extension.
EXPECTED = {
    "/v1/verdict": (
        VERDICT_INPUT_SCHEMA,
        VERDICT_INPUT_EXAMPLE,
        VERDICT_OUTPUT_SCHEMA,
        VERDICT_OUTPUT_EXAMPLE,
    ),
    "/v1/pre-trade": (
        PRE_TRADE_INPUT_SCHEMA,
        PRE_TRADE_COMPACT_INPUT_EXAMPLE,
        PRE_TRADE_OUTPUT_SCHEMA,
        PRE_TRADE_COMPACT_OUTPUT_EXAMPLE,
    ),
    "/v1/batch-verdict": (
        BATCH_INPUT_SCHEMA,
        BATCH_COMPACT_INPUT_EXAMPLE,
        BATCH_OUTPUT_SCHEMA,
        BATCH_COMPACT_OUTPUT_EXAMPLE,
    ),
}


def _priced_resources():
    settings = load_settings()
    manifest = x402_manifest(settings, BASE)
    return {
        r["resource"][len(BASE):]: r
        for r in manifest["resources"]
        if r.get("method") == "POST"
    }


def test_manifest_carries_examples_for_all_priced_routes():
    resources = _priced_resources()
    assert set(resources) == set(EXPECTED)
    for path, (in_schema, in_example, out_schema, out_example) in EXPECTED.items():
        resource = resources[path]
        # The example rides INSIDE each schema under the standard
        # JSON-Schema "example" keyword, verbatim from the constants
        # the Bazaar extension already publishes.
        assert resource["inputSchema"] == {**in_schema, "example": in_example}
        assert resource["outputSchema"] == {**out_schema, "example": out_example}
        # And each embedded example is valid against its own schema.
        jsonschema.validate(instance=in_example, schema=in_schema)
        jsonschema.validate(instance=out_example, schema=out_schema)


def test_embedding_does_not_mutate_the_schema_constants():
    _priced_resources()
    for in_schema, _, out_schema, _ in EXPECTED.values():
        assert "example" not in in_schema
        assert "example" not in out_schema


def test_agent_card_skills_carry_input_examples():
    settings = load_settings()
    card = agent_card(settings, BASE)
    assert len(card["skills"]) == len(EXPECTED)
    by_id = {s["id"]: s for s in card["skills"]}
    for path, (_, in_example, _, _) in EXPECTED.items():
        skill = by_id[path.strip("/").replace("/", "-")]
        # A2A shape (stall-row card): a list holding the input
        # example as a JSON string.
        assert skill["examples"] == [json.dumps(in_example)]
        assert json.loads(skill["examples"][0]) == in_example
