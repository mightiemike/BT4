# Q651: ECKey: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `ECKey.recoverFromSignature` in `crypto/src/main/java/org/tron/common/crypto/ECKey.java` — where the attacker collects signatures from ECKey.recoverFromSignature to detect a reused or biased k allowing key recovery — to break the invariant that ECKey.recoverFromSignature uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/ECKey.java` -> `ECKey.recoverFromSignature`
- Entrypoint: collect signatures produced via ECKey.recoverFromSignature
- Attacker controls: request/transaction/contract inputs to `ECKey.recoverFromSignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from ECKey.recoverFromSignature to detect a reused or biased k allowing key recovery
- Invariant to test: ECKey.recoverFromSignature uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
