# Q3459: state-selection mismatch in Util.getVisible

## Question
Can an unprivileged attacker use /wallet/* public HTTP APIs so framework/src/main/java/org/tron/core/services/http/Util.java::getVisible selects a stale, pending, or wrong block/account view for one step and a different view for the next, letting the user chain reads and writes into Execution or state selection against the wrong account or contract context?

## Target
- File/function: framework/src/main/java/org/tron/core/services/http/Util.java::getVisible
- Entrypoint: /wallet/* public HTTP APIs
- Attacker controls: RPC params, block tags and ranges, topic arrays, filter ids, raw hex, pagination, and visible/base58/hex encoding
- Exploit idea: Probe latest/pending tags, empty or boundary block params, range endpoints, and code paths that fall back between stores.
- Invariant to test: A public API must resolve one coherent block/account context per request and that context must match the later settlement path it feeds.
- Expected Immunefi impact: Execution or state selection against the wrong account or contract context
- Fast validation: Compare outputs across latest/pending/boundary parameters via /wallet/* public HTTP APIs, then chain the corresponding write path and assert the same state source is used end-to-end.
