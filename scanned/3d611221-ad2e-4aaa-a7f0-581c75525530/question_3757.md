# Q3757: raw-input canonicalization in RuntimeData.getRemoteAddr

## Question
Can an unprivileged attacker submit ambiguous public input through /wallet/estimateenergy so framework/src/main/java/org/tron/core/services/ratelimiter/RuntimeData.java::getRemoteAddr decodes one account, contract, or payload for validation but a different one for execution or broadcast, resulting in Unauthorized internal value movement or state mutation?

## Target
- File/function: framework/src/main/java/org/tron/core/services/ratelimiter/RuntimeData.java::getRemoteAddr
- Entrypoint: /wallet/estimateenergy
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Target visible/base58/hex decoding, JSON-RPC data/input handling, raw-hex parsing, and any alternate public encoding of the same request.
- Invariant to test: All public APIs must canonicalize and validate one unique payload before any later execution or broadcast path consumes it.
- Expected Immunefi impact: Unauthorized internal value movement or state mutation
- Fast validation: Replay the same logical request across all accepted encodings via /wallet/estimateenergy; assert the resulting unsigned/signed tx bytes and selected objects are identical.
