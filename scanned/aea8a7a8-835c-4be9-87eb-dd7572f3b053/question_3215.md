# Q3215: BN128: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128.b` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128.java` — where the attacker passes an off-curve or identity point to BN128.b causing wrong verification or a crash — to break the invariant that BN128.b validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128.java` -> `BN128.b`
- Entrypoint: precompile/verify path to BN128.b
- Attacker controls: request/transaction/contract inputs to `BN128.b` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to BN128.b causing wrong verification or a crash
- Invariant to test: BN128.b validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
