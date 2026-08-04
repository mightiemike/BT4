# Q3767: visible-encoding object mixup in RuntimeData.getRemoteAddr

## Question
Can an unprivileged attacker abuse visible/base58/hex or key-format handling through /wallet/estimateenergy so framework/src/main/java/org/tron/core/services/ratelimiter/RuntimeData.java::getRemoteAddr returns or targets the wrong account, storage slot, or contract, and a user can chain that confusion into Unauthorized internal value movement or state mutation?

## Target
- File/function: framework/src/main/java/org/tron/core/services/ratelimiter/RuntimeData.java::getRemoteAddr
- Entrypoint: /wallet/estimateenergy
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Try equivalent-looking addresses, storage keys, and payload fields in every accepted encoding form.
- Invariant to test: Every accepted public encoding must resolve to exactly one internal object and every API surface must agree on that resolution.
- Expected Immunefi impact: Unauthorized internal value movement or state mutation
- Fast validation: Use alternate encodings through /wallet/estimateenergy; assert the same object is read, built into tx data, and later executed against.
