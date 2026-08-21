# Q1: BN128G2: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128G2.toAffine` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G2.java` — where the attacker passes an off-curve or identity point to BN128G2.toAffine causing wrong verification or a crash — to break the invariant that BN128G2.toAffine validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G2.java` -> `BN128G2.toAffine`
- Entrypoint: precompile/verify path to BN128G2.toAffine
- Attacker controls: request/transaction/contract inputs to `BN128G2.toAffine` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to BN128G2.toAffine causing wrong verification or a crash
- Invariant to test: BN128G2.toAffine validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
