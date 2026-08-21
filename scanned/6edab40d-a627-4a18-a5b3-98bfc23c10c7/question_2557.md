# Q2557: ECKey: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `ECKey.signatureToKey` in `crypto/src/main/java/org/tron/common/crypto/ECKey.java` — where the attacker collects signatures from ECKey.signatureToKey to detect a reused or biased k allowing key recovery — to break the invariant that ECKey.signatureToKey uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/ECKey.java` -> `ECKey.signatureToKey`
- Entrypoint: collect signatures produced via ECKey.signatureToKey
- Attacker controls: request/transaction/contract inputs to `ECKey.signatureToKey` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from ECKey.signatureToKey to detect a reused or biased k allowing key recovery
- Invariant to test: ECKey.signatureToKey uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
