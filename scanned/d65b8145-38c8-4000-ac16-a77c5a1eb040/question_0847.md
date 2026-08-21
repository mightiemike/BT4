# Q847: Fp2: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Fp2.squared` in `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp2.java` — where the attacker collects signatures from Fp2.squared to detect a reused or biased k allowing key recovery — to break the invariant that Fp2.squared uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp2.java` -> `Fp2.squared`
- Entrypoint: collect signatures produced via Fp2.squared
- Attacker controls: request/transaction/contract inputs to `Fp2.squared` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from Fp2.squared to detect a reused or biased k allowing key recovery
- Invariant to test: Fp2.squared uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
