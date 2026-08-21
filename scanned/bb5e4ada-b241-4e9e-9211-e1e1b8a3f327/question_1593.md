# Q1593: SM2: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SM2.decompressPoint` in `crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java` — where the attacker passes an off-curve or identity point to SM2.decompressPoint causing wrong verification or a crash — to break the invariant that SM2.decompressPoint validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java` -> `SM2.decompressPoint`
- Entrypoint: precompile/verify path to SM2.decompressPoint
- Attacker controls: request/transaction/contract inputs to `SM2.decompressPoint` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to SM2.decompressPoint causing wrong verification or a crash
- Invariant to test: SM2.decompressPoint validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
