# Q2839: KeyIo: nullifier/anchor reuse

## Question
Can an unprivileged attacker (shielded transaction) abuse `KeyIo.convertBits` in `framework/src/main/java/org/tron/core/zen/address/KeyIo.java` — where the attacker replays a nullifier or stale anchor through KeyIo.convertBits to double-spend a shielded note — to break the invariant that each nullifier is accepted once and anchors must be current in KeyIo.convertBits, leading to: Asset theft (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/address/KeyIo.java` -> `KeyIo.convertBits`
- Entrypoint: shielded spend to KeyIo.convertBits with reused nullifier
- Attacker controls: request/transaction/contract inputs to `KeyIo.convertBits` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a nullifier or stale anchor through KeyIo.convertBits to double-spend a shielded note
- Invariant to test: each nullifier is accepted once and anchors must be current in KeyIo.convertBits
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit replaying nullifier asserting rejection
