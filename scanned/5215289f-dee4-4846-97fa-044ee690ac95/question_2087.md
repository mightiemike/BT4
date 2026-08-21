# Q2087: PairingCheck: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `PairingCheck.create` in `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` — where the attacker collects signatures from PairingCheck.create to detect a reused or biased k allowing key recovery — to break the invariant that PairingCheck.create uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` -> `PairingCheck.create`
- Entrypoint: collect signatures produced via PairingCheck.create
- Attacker controls: request/transaction/contract inputs to `PairingCheck.create` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from PairingCheck.create to detect a reused or biased k allowing key recovery
- Invariant to test: PairingCheck.create uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
