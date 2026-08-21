# Q1883: ECKey: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `ECKey.signatureToKeyBytes` in `crypto/src/main/java/org/tron/common/crypto/ECKey.java` — where the attacker collects signatures from ECKey.signatureToKeyBytes to detect a reused or biased k allowing key recovery — to break the invariant that ECKey.signatureToKeyBytes uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/ECKey.java` -> `ECKey.signatureToKeyBytes`
- Entrypoint: collect signatures produced via ECKey.signatureToKeyBytes
- Attacker controls: request/transaction/contract inputs to `ECKey.signatureToKeyBytes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from ECKey.signatureToKeyBytes to detect a reused or biased k allowing key recovery
- Invariant to test: ECKey.signatureToKeyBytes uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
