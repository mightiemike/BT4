# Q2339: Fp: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Fp.sub` in `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` — where the attacker collects signatures from Fp.sub to detect a reused or biased k allowing key recovery — to break the invariant that Fp.sub uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` -> `Fp.sub`
- Entrypoint: collect signatures produced via Fp.sub
- Attacker controls: request/transaction/contract inputs to `Fp.sub` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from Fp.sub to detect a reused or biased k allowing key recovery
- Invariant to test: Fp.sub uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
