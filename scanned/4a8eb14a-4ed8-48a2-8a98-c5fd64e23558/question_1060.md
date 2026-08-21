# Q1060: ShieldedTRC20ParametersBuilder: nullifier/anchor reuse

## Question
Can an unprivileged attacker (shielded transaction) abuse `ShieldedTRC20ParametersBuilder.formatPath` in `framework/src/main/java/org/tron/core/zen/ShieldedTRC20ParametersBuilder.java` — where the attacker replays a nullifier or stale anchor through ShieldedTRC20ParametersBuilder.formatPath to double-spend a shielded note — to break the invariant that each nullifier is accepted once and anchors must be current in ShieldedTRC20ParametersBuilder.formatPath, leading to: Asset theft (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/ShieldedTRC20ParametersBuilder.java` -> `ShieldedTRC20ParametersBuilder.formatPath`
- Entrypoint: shielded spend to ShieldedTRC20ParametersBuilder.formatPath with reused nullifier
- Attacker controls: request/transaction/contract inputs to `ShieldedTRC20ParametersBuilder.formatPath` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a nullifier or stale anchor through ShieldedTRC20ParametersBuilder.formatPath to double-spend a shielded note
- Invariant to test: each nullifier is accepted once and anchors must be current in ShieldedTRC20ParametersBuilder.formatPath
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit replaying nullifier asserting rejection
