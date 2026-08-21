# Q289: ZenTransactionBuilder: nullifier/anchor reuse

## Question
Can an unprivileged attacker (shielded transaction) abuse `ZenTransactionBuilder.addOutput` in `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` — where the attacker replays a nullifier or stale anchor through ZenTransactionBuilder.addOutput to double-spend a shielded note — to break the invariant that each nullifier is accepted once and anchors must be current in ZenTransactionBuilder.addOutput, leading to: Asset theft (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` -> `ZenTransactionBuilder.addOutput`
- Entrypoint: shielded spend to ZenTransactionBuilder.addOutput with reused nullifier
- Attacker controls: request/transaction/contract inputs to `ZenTransactionBuilder.addOutput` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a nullifier or stale anchor through ZenTransactionBuilder.addOutput to double-spend a shielded note
- Invariant to test: each nullifier is accepted once and anchors must be current in ZenTransactionBuilder.addOutput
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit replaying nullifier asserting rejection
