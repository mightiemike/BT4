# Q3718: BN128Fp: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128Fp.one` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128Fp.java` — where the attacker collects signatures from BN128Fp.one to detect a reused or biased k allowing key recovery — to break the invariant that BN128Fp.one uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128Fp.java` -> `BN128Fp.one`
- Entrypoint: collect signatures produced via BN128Fp.one
- Attacker controls: request/transaction/contract inputs to `BN128Fp.one` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from BN128Fp.one to detect a reused or biased k allowing key recovery
- Invariant to test: BN128Fp.one uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
