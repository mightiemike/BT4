# Q3610: Hash: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Hash.computeAddress` in `crypto/src/main/java/org/tron/common/crypto/Hash.java` — where the attacker passes an off-curve or identity point to Hash.computeAddress causing wrong verification or a crash — to break the invariant that Hash.computeAddress validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Hash.java` -> `Hash.computeAddress`
- Entrypoint: precompile/verify path to Hash.computeAddress
- Attacker controls: request/transaction/contract inputs to `Hash.computeAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to Hash.computeAddress causing wrong verification or a crash
- Invariant to test: Hash.computeAddress validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
