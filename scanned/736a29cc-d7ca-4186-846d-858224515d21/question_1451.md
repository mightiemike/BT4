# Q1451: Fp: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Fp.squared` in `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` — where the attacker passes an off-curve or identity point to Fp.squared causing wrong verification or a crash — to break the invariant that Fp.squared validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` -> `Fp.squared`
- Entrypoint: precompile/verify path to Fp.squared
- Attacker controls: request/transaction/contract inputs to `Fp.squared` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to Fp.squared causing wrong verification or a crash
- Invariant to test: Fp.squared validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
