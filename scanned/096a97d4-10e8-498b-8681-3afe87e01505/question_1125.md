# Q1125: JLibrustzcash: nullifier/anchor reuse

## Question
Can an unprivileged attacker (shielded transaction) abuse `JLibrustzcash.librustzcashToScalar` in `chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java` — where the attacker replays a nullifier or stale anchor through JLibrustzcash.librustzcashToScalar to double-spend a shielded note — to break the invariant that each nullifier is accepted once and anchors must be current in JLibrustzcash.librustzcashToScalar, leading to: Asset theft (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java` -> `JLibrustzcash.librustzcashToScalar`
- Entrypoint: shielded spend to JLibrustzcash.librustzcashToScalar with reused nullifier
- Attacker controls: request/transaction/contract inputs to `JLibrustzcash.librustzcashToScalar` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a nullifier or stale anchor through JLibrustzcash.librustzcashToScalar to double-spend a shielded note
- Invariant to test: each nullifier is accepted once and anchors must be current in JLibrustzcash.librustzcashToScalar
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit replaying nullifier asserting rejection
