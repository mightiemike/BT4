# Q3172: PairingCheck: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `PairingCheck.millerLoop` in `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` — where the attacker collects signatures from PairingCheck.millerLoop to detect a reused or biased k allowing key recovery — to break the invariant that PairingCheck.millerLoop uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` -> `PairingCheck.millerLoop`
- Entrypoint: collect signatures produced via PairingCheck.millerLoop
- Attacker controls: request/transaction/contract inputs to `PairingCheck.millerLoop` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from PairingCheck.millerLoop to detect a reused or biased k allowing key recovery
- Invariant to test: PairingCheck.millerLoop uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
