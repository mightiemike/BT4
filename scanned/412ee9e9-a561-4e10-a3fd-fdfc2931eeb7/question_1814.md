# Q1814: ZenTransactionBuilder: nullifier/anchor reuse

## Question
Can an unprivileged attacker (shielded transaction) abuse `ZenTransactionBuilder.generateSpendProof` in `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` — where the attacker replays a nullifier or stale anchor through ZenTransactionBuilder.generateSpendProof to double-spend a shielded note — to break the invariant that each nullifier is accepted once and anchors must be current in ZenTransactionBuilder.generateSpendProof, leading to: Asset theft (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` -> `ZenTransactionBuilder.generateSpendProof`
- Entrypoint: shielded spend to ZenTransactionBuilder.generateSpendProof with reused nullifier
- Attacker controls: request/transaction/contract inputs to `ZenTransactionBuilder.generateSpendProof` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a nullifier or stale anchor through ZenTransactionBuilder.generateSpendProof to double-spend a shielded note
- Invariant to test: each nullifier is accepted once and anchors must be current in ZenTransactionBuilder.generateSpendProof
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit replaying nullifier asserting rejection
