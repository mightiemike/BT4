# Q3823: Fp2: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Fp2.inverse` in `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp2.java` — where the attacker passes an off-curve or identity point to Fp2.inverse causing wrong verification or a crash — to break the invariant that Fp2.inverse validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp2.java` -> `Fp2.inverse`
- Entrypoint: precompile/verify path to Fp2.inverse
- Attacker controls: request/transaction/contract inputs to `Fp2.inverse` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to Fp2.inverse causing wrong verification or a crash
- Invariant to test: Fp2.inverse validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
