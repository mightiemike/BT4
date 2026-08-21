# Q94: BN128G1: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128G1.toAffine` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G1.java` — where the attacker collects signatures from BN128G1.toAffine to detect a reused or biased k allowing key recovery — to break the invariant that BN128G1.toAffine uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G1.java` -> `BN128G1.toAffine`
- Entrypoint: collect signatures produced via BN128G1.toAffine
- Attacker controls: request/transaction/contract inputs to `BN128G1.toAffine` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from BN128G1.toAffine to detect a reused or biased k allowing key recovery
- Invariant to test: BN128G1.toAffine uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
