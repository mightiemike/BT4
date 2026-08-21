# Q3893: BN128G1: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128G1.create` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G1.java` — where the attacker passes an off-curve or identity point to BN128G1.create causing wrong verification or a crash — to break the invariant that BN128G1.create validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G1.java` -> `BN128G1.create`
- Entrypoint: precompile/verify path to BN128G1.create
- Attacker controls: request/transaction/contract inputs to `BN128G1.create` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to BN128G1.create causing wrong verification or a crash
- Invariant to test: BN128G1.create validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
