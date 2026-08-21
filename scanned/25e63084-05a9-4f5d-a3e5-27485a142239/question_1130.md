# Q1130: Rsv: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Rsv.fromSignature` in `crypto/src/main/java/org/tron/common/crypto/Rsv.java` — where the attacker collects signatures from Rsv.fromSignature to detect a reused or biased k allowing key recovery — to break the invariant that Rsv.fromSignature uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Rsv.java` -> `Rsv.fromSignature`
- Entrypoint: collect signatures produced via Rsv.fromSignature
- Attacker controls: request/transaction/contract inputs to `Rsv.fromSignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from Rsv.fromSignature to detect a reused or biased k allowing key recovery
- Invariant to test: Rsv.fromSignature uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
