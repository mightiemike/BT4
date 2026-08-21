# Q1132: BN128G1: recovery id / v confusion

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128G1.create` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G1.java` — where the attacker manipulates the recovery byte so BN128G1.create recovers an unintended address the attacker can predict — to break the invariant that BN128G1.create recovers exactly one address for a valid signature, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G1.java` -> `BN128G1.create`
- Entrypoint: path calling BN128G1.create with crafted v
- Attacker controls: request/transaction/contract inputs to `BN128G1.create` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: manipulates the recovery byte so BN128G1.create recovers an unintended address the attacker can predict
- Invariant to test: BN128G1.create recovers exactly one address for a valid signature
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit varying v and asserting single valid recovery
