# Q2196: SM2: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SM2.getSM2SignerForHash` in `crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java` — where the attacker collects signatures from SM2.getSM2SignerForHash to detect a reused or biased k allowing key recovery — to break the invariant that SM2.getSM2SignerForHash uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java` -> `SM2.getSM2SignerForHash`
- Entrypoint: collect signatures produced via SM2.getSM2SignerForHash
- Attacker controls: request/transaction/contract inputs to `SM2.getSM2SignerForHash` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from SM2.getSM2SignerForHash to detect a reused or biased k allowing key recovery
- Invariant to test: SM2.getSM2SignerForHash uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
