# Q3117: ZenTransactionBuilder: nullifier/anchor reuse

## Question
Can an unprivileged attacker (shielded transaction) abuse `ZenTransactionBuilder.buildWithoutAsk` in `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` — where the attacker replays a nullifier or stale anchor through ZenTransactionBuilder.buildWithoutAsk to double-spend a shielded note — to break the invariant that each nullifier is accepted once and anchors must be current in ZenTransactionBuilder.buildWithoutAsk, leading to: Asset theft (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` -> `ZenTransactionBuilder.buildWithoutAsk`
- Entrypoint: shielded spend to ZenTransactionBuilder.buildWithoutAsk with reused nullifier
- Attacker controls: request/transaction/contract inputs to `ZenTransactionBuilder.buildWithoutAsk` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a nullifier or stale anchor through ZenTransactionBuilder.buildWithoutAsk to double-spend a shielded note
- Invariant to test: each nullifier is accepted once and anchors must be current in ZenTransactionBuilder.buildWithoutAsk
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit replaying nullifier asserting rejection
