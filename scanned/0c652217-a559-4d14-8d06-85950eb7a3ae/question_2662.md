# Q2662: Credentials: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Credentials.getSignInterface` in `crypto/src/main/java/org/tron/keystore/Credentials.java` — where the attacker passes an off-curve or identity point to Credentials.getSignInterface causing wrong verification or a crash — to break the invariant that Credentials.getSignInterface validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/Credentials.java` -> `Credentials.getSignInterface`
- Entrypoint: precompile/verify path to Credentials.getSignInterface
- Attacker controls: request/transaction/contract inputs to `Credentials.getSignInterface` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to Credentials.getSignInterface causing wrong verification or a crash
- Invariant to test: Credentials.getSignInterface validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
