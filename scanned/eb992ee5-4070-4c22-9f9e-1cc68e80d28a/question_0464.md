# Q464: Fp: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Fp.mul` in `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` — where the attacker passes an off-curve or identity point to Fp.mul causing wrong verification or a crash — to break the invariant that Fp.mul validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` -> `Fp.mul`
- Entrypoint: precompile/verify path to Fp.mul
- Attacker controls: request/transaction/contract inputs to `Fp.mul` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to Fp.mul causing wrong verification or a crash
- Invariant to test: Fp.mul validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
