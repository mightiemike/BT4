# Q173: JLibrustzcash: nullifier/anchor reuse

## Question
Can an unprivileged attacker (shielded transaction) abuse `JLibrustzcash.librustzcashNskToNk` in `chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java` — where the attacker replays a nullifier or stale anchor through JLibrustzcash.librustzcashNskToNk to double-spend a shielded note — to break the invariant that each nullifier is accepted once and anchors must be current in JLibrustzcash.librustzcashNskToNk, leading to: Asset theft (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java` -> `JLibrustzcash.librustzcashNskToNk`
- Entrypoint: shielded spend to JLibrustzcash.librustzcashNskToNk with reused nullifier
- Attacker controls: request/transaction/contract inputs to `JLibrustzcash.librustzcashNskToNk` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a nullifier or stale anchor through JLibrustzcash.librustzcashNskToNk to double-spend a shielded note
- Invariant to test: each nullifier is accepted once and anchors must be current in JLibrustzcash.librustzcashNskToNk
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit replaying nullifier asserting rejection
