# Q1878: Fp2: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Fp2.add` in `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp2.java` — where the attacker collects signatures from Fp2.add to detect a reused or biased k allowing key recovery — to break the invariant that Fp2.add uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp2.java` -> `Fp2.add`
- Entrypoint: collect signatures produced via Fp2.add
- Attacker controls: request/transaction/contract inputs to `Fp2.add` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from Fp2.add to detect a reused or biased k allowing key recovery
- Invariant to test: Fp2.add uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
