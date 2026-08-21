# Q1716: SM2: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SM2.signMsg` in `crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java` — where the attacker collects signatures from SM2.signMsg to detect a reused or biased k allowing key recovery — to break the invariant that SM2.signMsg uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java` -> `SM2.signMsg`
- Entrypoint: collect signatures produced via SM2.signMsg
- Attacker controls: request/transaction/contract inputs to `SM2.signMsg` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from SM2.signMsg to detect a reused or biased k allowing key recovery
- Invariant to test: SM2.signMsg uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
