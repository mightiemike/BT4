# Q1710: ECKey: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `ECKey.compressPoint` in `crypto/src/main/java/org/tron/common/crypto/ECKey.java` — where the attacker passes an off-curve or identity point to ECKey.compressPoint causing wrong verification or a crash — to break the invariant that ECKey.compressPoint validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/ECKey.java` -> `ECKey.compressPoint`
- Entrypoint: precompile/verify path to ECKey.compressPoint
- Attacker controls: request/transaction/contract inputs to `ECKey.compressPoint` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to ECKey.compressPoint causing wrong verification or a crash
- Invariant to test: ECKey.compressPoint validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
