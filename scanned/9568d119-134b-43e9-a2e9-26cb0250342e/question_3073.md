# Q3073: Rsv: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Rsv.fromSignature` in `crypto/src/main/java/org/tron/common/crypto/Rsv.java` — where the attacker passes an off-curve or identity point to Rsv.fromSignature causing wrong verification or a crash — to break the invariant that Rsv.fromSignature validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Rsv.java` -> `Rsv.fromSignature`
- Entrypoint: precompile/verify path to Rsv.fromSignature
- Attacker controls: request/transaction/contract inputs to `Rsv.fromSignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to Rsv.fromSignature causing wrong verification or a crash
- Invariant to test: Rsv.fromSignature validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
