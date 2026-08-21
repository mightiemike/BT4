# Q1621: LibrustzcashParam: nullifier/anchor reuse

## Question
Can an unprivileged attacker (shielded transaction) abuse `LibrustzcashParam.validNull` in `chainbase/src/main/java/org/tron/common/zksnark/LibrustzcashParam.java` — where the attacker replays a nullifier or stale anchor through LibrustzcashParam.validNull to double-spend a shielded note — to break the invariant that each nullifier is accepted once and anchors must be current in LibrustzcashParam.validNull, leading to: Asset theft (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/LibrustzcashParam.java` -> `LibrustzcashParam.validNull`
- Entrypoint: shielded spend to LibrustzcashParam.validNull with reused nullifier
- Attacker controls: request/transaction/contract inputs to `LibrustzcashParam.validNull` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a nullifier or stale anchor through LibrustzcashParam.validNull to double-spend a shielded note
- Invariant to test: each nullifier is accepted once and anchors must be current in LibrustzcashParam.validNull
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit replaying nullifier asserting rejection
