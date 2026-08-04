# Q3762: api-surface inconsistency in RuntimeData.getRemoteAddr

## Question
Can an unprivileged attacker invoke the same logical action through /wallet/deploycontract -> sign -> /wallet/broadcasttransaction and an alternate HTTP/gRPC/JSON-RPC path so framework/src/main/java/org/tron/core/services/ratelimiter/RuntimeData.java::getRemoteAddr applies different normalization or guard logic, with the weaker path leading to Unauthorized internal value movement or state mutation?

## Target
- File/function: framework/src/main/java/org/tron/core/services/ratelimiter/RuntimeData.java::getRemoteAddr
- Entrypoint: /wallet/deploycontract -> sign -> /wallet/broadcasttransaction
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Cross-check every public surface that can build, simulate, or broadcast the same transaction or query.
- Invariant to test: Equivalent public surfaces must normalize the same fields, enforce the same guards, and return the same decision for one logical action.
- Expected Immunefi impact: Unauthorized internal value movement or state mutation
- Fast validation: Replay identical inputs through all public surfaces and diff selected owner, contract, tx bytes, and rejection reasons.
