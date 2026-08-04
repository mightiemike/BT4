# Q3759: state-selection mismatch in RuntimeData.getRemoteAddr

## Question
Can an unprivileged attacker use /wallet/triggerconstantcontract so framework/src/main/java/org/tron/core/services/ratelimiter/RuntimeData.java::getRemoteAddr selects a stale, pending, or wrong block/account view for one step and a different view for the next, letting the user chain reads and writes into Unauthorized internal value movement or state mutation?

## Target
- File/function: framework/src/main/java/org/tron/core/services/ratelimiter/RuntimeData.java::getRemoteAddr
- Entrypoint: /wallet/triggerconstantcontract
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Probe latest/pending tags, empty or boundary block params, range endpoints, and code paths that fall back between stores.
- Invariant to test: A public API must resolve one coherent block/account context per request and that context must match the later settlement path it feeds.
- Expected Immunefi impact: Unauthorized internal value movement or state mutation
- Fast validation: Compare outputs across latest/pending/boundary parameters via /wallet/triggerconstantcontract, then chain the corresponding write path and assert the same state source is used end-to-end.
