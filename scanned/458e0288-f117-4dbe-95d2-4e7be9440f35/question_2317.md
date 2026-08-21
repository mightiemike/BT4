# Q2317: DelegatedResourceAccountIndexCapsule: vote weight inflation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexCapsule.removeFromAccount` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` — where the attacker inflates vote weight through DelegatedResourceAccountIndexCapsule.removeFromAccount beyond frozen stake — to break the invariant that votes counted in DelegatedResourceAccountIndexCapsule.removeFromAccount never exceed the voter's frozen stake, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` -> `DelegatedResourceAccountIndexCapsule.removeFromAccount`
- Entrypoint: broadcast votes via DelegatedResourceAccountIndexCapsule.removeFromAccount
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexCapsule.removeFromAccount` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates vote weight through DelegatedResourceAccountIndexCapsule.removeFromAccount beyond frozen stake
- Invariant to test: votes counted in DelegatedResourceAccountIndexCapsule.removeFromAccount never exceed the voter's frozen stake
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit voting beyond stake asserting rejection
