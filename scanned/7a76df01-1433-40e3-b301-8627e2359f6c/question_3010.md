# Q3010: Fp: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Fp.inverse` in `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` — where the attacker collects signatures from Fp.inverse to detect a reused or biased k allowing key recovery — to break the invariant that Fp.inverse uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` -> `Fp.inverse`
- Entrypoint: collect signatures produced via Fp.inverse
- Attacker controls: request/transaction/contract inputs to `Fp.inverse` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from Fp.inverse to detect a reused or biased k allowing key recovery
- Invariant to test: Fp.inverse uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
