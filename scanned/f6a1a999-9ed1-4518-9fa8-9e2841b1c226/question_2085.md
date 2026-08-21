# Q2085: ECKey: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `ECKey.recoverAddressFromSignature` in `crypto/src/main/java/org/tron/common/crypto/ECKey.java` — where the attacker passes an off-curve or identity point to ECKey.recoverAddressFromSignature causing wrong verification or a crash — to break the invariant that ECKey.recoverAddressFromSignature validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/ECKey.java` -> `ECKey.recoverAddressFromSignature`
- Entrypoint: precompile/verify path to ECKey.recoverAddressFromSignature
- Attacker controls: request/transaction/contract inputs to `ECKey.recoverAddressFromSignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to ECKey.recoverAddressFromSignature causing wrong verification or a crash
- Invariant to test: ECKey.recoverAddressFromSignature validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
