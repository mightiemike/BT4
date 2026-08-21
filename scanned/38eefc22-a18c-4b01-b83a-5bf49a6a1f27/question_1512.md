# Q1512: Fp: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Fp.squared` in `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` — where the attacker collects signatures from Fp.squared to detect a reused or biased k allowing key recovery — to break the invariant that Fp.squared uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` -> `Fp.squared`
- Entrypoint: collect signatures produced via Fp.squared
- Attacker controls: request/transaction/contract inputs to `Fp.squared` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from Fp.squared to detect a reused or biased k allowing key recovery
- Invariant to test: Fp.squared uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
