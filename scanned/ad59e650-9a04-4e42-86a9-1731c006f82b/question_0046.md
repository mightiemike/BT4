# Q46: SM2: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SM2.getSigner` in `crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java` — where the attacker collects signatures from SM2.getSigner to detect a reused or biased k allowing key recovery — to break the invariant that SM2.getSigner uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java` -> `SM2.getSigner`
- Entrypoint: collect signatures produced via SM2.getSigner
- Attacker controls: request/transaction/contract inputs to `SM2.getSigner` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from SM2.getSigner to detect a reused or biased k allowing key recovery
- Invariant to test: SM2.getSigner uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
