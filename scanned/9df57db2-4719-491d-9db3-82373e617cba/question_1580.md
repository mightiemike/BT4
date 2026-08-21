# Q1580: ECKey: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `ECKey.recoverFromSignature` in `crypto/src/main/java/org/tron/common/crypto/ECKey.java` — where the attacker passes an off-curve or identity point to ECKey.recoverFromSignature causing wrong verification or a crash — to break the invariant that ECKey.recoverFromSignature validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/ECKey.java` -> `ECKey.recoverFromSignature`
- Entrypoint: precompile/verify path to ECKey.recoverFromSignature
- Attacker controls: request/transaction/contract inputs to `ECKey.recoverFromSignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to ECKey.recoverFromSignature causing wrong verification or a crash
- Invariant to test: ECKey.recoverFromSignature validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
