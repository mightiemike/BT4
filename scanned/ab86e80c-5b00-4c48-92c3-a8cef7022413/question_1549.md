# Q1549: BN128Fp: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128Fp.zero` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128Fp.java` — where the attacker passes an off-curve or identity point to BN128Fp.zero causing wrong verification or a crash — to break the invariant that BN128Fp.zero validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128Fp.java` -> `BN128Fp.zero`
- Entrypoint: precompile/verify path to BN128Fp.zero
- Attacker controls: request/transaction/contract inputs to `BN128Fp.zero` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to BN128Fp.zero causing wrong verification or a crash
- Invariant to test: BN128Fp.zero validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
