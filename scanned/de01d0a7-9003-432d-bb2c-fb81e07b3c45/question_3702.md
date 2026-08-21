# Q3702: ECKey: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `ECKey.fromPublicOnly` in `crypto/src/main/java/org/tron/common/crypto/ECKey.java` — where the attacker passes an off-curve or identity point to ECKey.fromPublicOnly causing wrong verification or a crash — to break the invariant that ECKey.fromPublicOnly validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/ECKey.java` -> `ECKey.fromPublicOnly`
- Entrypoint: precompile/verify path to ECKey.fromPublicOnly
- Attacker controls: request/transaction/contract inputs to `ECKey.fromPublicOnly` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to ECKey.fromPublicOnly causing wrong verification or a crash
- Invariant to test: ECKey.fromPublicOnly validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
