# Q1508: Fp: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Fp.dbl` in `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` — where the attacker passes an off-curve or identity point to Fp.dbl causing wrong verification or a crash — to break the invariant that Fp.dbl validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` -> `Fp.dbl`
- Entrypoint: precompile/verify path to Fp.dbl
- Attacker controls: request/transaction/contract inputs to `Fp.dbl` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to Fp.dbl causing wrong verification or a crash
- Invariant to test: Fp.dbl validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
