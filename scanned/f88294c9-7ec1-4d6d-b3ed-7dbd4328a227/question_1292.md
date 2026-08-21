# Q1292: BN128G2: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128G2.create` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G2.java` — where the attacker collects signatures from BN128G2.create to detect a reused or biased k allowing key recovery — to break the invariant that BN128G2.create uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G2.java` -> `BN128G2.create`
- Entrypoint: collect signatures produced via BN128G2.create
- Attacker controls: request/transaction/contract inputs to `BN128G2.create` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from BN128G2.create to detect a reused or biased k allowing key recovery
- Invariant to test: BN128G2.create uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
