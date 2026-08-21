# Q67: BN128Fp: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128Fp.b` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128Fp.java` — where the attacker collects signatures from BN128Fp.b to detect a reused or biased k allowing key recovery — to break the invariant that BN128Fp.b uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128Fp.java` -> `BN128Fp.b`
- Entrypoint: collect signatures produced via BN128Fp.b
- Attacker controls: request/transaction/contract inputs to `BN128Fp.b` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from BN128Fp.b to detect a reused or biased k allowing key recovery
- Invariant to test: BN128Fp.b uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
