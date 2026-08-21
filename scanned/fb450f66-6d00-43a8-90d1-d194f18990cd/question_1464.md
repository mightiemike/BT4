# Q1464: BN128: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128.b` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128.java` — where the attacker collects signatures from BN128.b to detect a reused or biased k allowing key recovery — to break the invariant that BN128.b uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128.java` -> `BN128.b`
- Entrypoint: collect signatures produced via BN128.b
- Attacker controls: request/transaction/contract inputs to `BN128.b` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from BN128.b to detect a reused or biased k allowing key recovery
- Invariant to test: BN128.b uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
