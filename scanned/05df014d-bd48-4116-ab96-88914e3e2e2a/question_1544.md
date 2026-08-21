# Q1544: Fp: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Fp.sub` in `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` — where the attacker passes an off-curve or identity point to Fp.sub causing wrong verification or a crash — to break the invariant that Fp.sub validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` -> `Fp.sub`
- Entrypoint: precompile/verify path to Fp.sub
- Attacker controls: request/transaction/contract inputs to `Fp.sub` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to Fp.sub causing wrong verification or a crash
- Invariant to test: Fp.sub validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
