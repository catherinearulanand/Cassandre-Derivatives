# ADR-006: Replay and rules are mandatory ClauseOS modes; local LLM optional

## Decision
ClauseOS's critical path never requires a model. Two proposers exist:
deterministic rule/template proposer (regex/structure over the normalized
extraction) and a replay proposer (fixed recorded outputs for pilot
demonstration). A local-model adapter may be added later; it can never be
required for authoritative publication and never publishes without
maker-checker approval either way.

## S1 implementation this session
Only the rule proposer and structural verifier are built. No model adapter
(`CLO-X01`) is implemented — consistent with Decision Register `DEC-CLO-01`.

## Update: model adapter added in a later session
`CLO-X01` is now implemented (`products/clauseos/src/clauseos/core/model_adapter.py`),
calling a local Ollama server (`llama3.2:1b`) over HTTP. It remains exactly
what this ADR always allowed: a third, strictly additive proposer alongside
rules and replay, never required for authoritative publication, and never
able to publish without the unchanged maker/checker review workflow. Its
abstention behavior: on any timeout, unreachable server, oversized/malformed
response, JSON-schema validation failure, or a quoted evidence span that
cannot be found verbatim in the source document, the adapter returns zero
candidates for the affected concept(s) rather than raising an error or
emitting a partial/best-guess assertion — the same "propose nothing" shape
`core.rule_proposer` already uses when its regexes find no match, not a
separate error/status object. Document content is always wrapped in
delimited, explicitly-labeled data in the prompt and is never treated as an
instruction to the model. Decision Register `DEC-CLO-01` is updated
accordingly.
