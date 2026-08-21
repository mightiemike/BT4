# Q1505: LibrustzcashParam: nullifier/anchor reuse

## Question
Can an unprivileged attacker (shielded transaction) abuse `LibrustzcashParam.validParamLength` in `chainbase/src/main/java/org/tron/common/zksnark/LibrustzcashParam.java` — where the attacker replays a nullifier or stale anchor through LibrustzcashParam.validParamLength to double-spend a shielded note — to break the invariant that each nullifier is accepted once and anchors must be current in LibrustzcashParam.validParamLength, leading to: Asset theft (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/LibrustzcashParam.java` -> `LibrustzcashParam.validParamLength`
- Entrypoint: shielded spend to LibrustzcashParam.validParamLength with reused nullifier
- Attacker controls: request/transaction/contract inputs to `LibrustzcashParam.validParamLength` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a nullifier or stale anchor through LibrustzcashParam.validParamLength to double-spend a shielded note
- Invariant to test: each nullifier is accepted once and anchors must be current in LibrustzcashParam.validParamLength
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit replaying nullifier asserting rejection
