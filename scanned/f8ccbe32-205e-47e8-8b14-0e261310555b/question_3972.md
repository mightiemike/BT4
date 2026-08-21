# Q3972: LibrustzcashParam: nullifier/anchor reuse

## Question
Can an unprivileged attacker (shielded transaction) abuse `LibrustzcashParam.validObjectNull` in `chainbase/src/main/java/org/tron/common/zksnark/LibrustzcashParam.java` — where the attacker replays a nullifier or stale anchor through LibrustzcashParam.validObjectNull to double-spend a shielded note — to break the invariant that each nullifier is accepted once and anchors must be current in LibrustzcashParam.validObjectNull, leading to: Asset theft (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/LibrustzcashParam.java` -> `LibrustzcashParam.validObjectNull`
- Entrypoint: shielded spend to LibrustzcashParam.validObjectNull with reused nullifier
- Attacker controls: request/transaction/contract inputs to `LibrustzcashParam.validObjectNull` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a nullifier or stale anchor through LibrustzcashParam.validObjectNull to double-spend a shielded note
- Invariant to test: each nullifier is accepted once and anchors must be current in LibrustzcashParam.validObjectNull
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit replaying nullifier asserting rejection
