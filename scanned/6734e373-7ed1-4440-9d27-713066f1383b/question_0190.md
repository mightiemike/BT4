# Q190: DelegatedResourceAccountIndexCapsule: vote weight inflation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexCapsule.addFromAccount` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` — where the attacker inflates vote weight through DelegatedResourceAccountIndexCapsule.addFromAccount beyond frozen stake — to break the invariant that votes counted in DelegatedResourceAccountIndexCapsule.addFromAccount never exceed the voter's frozen stake, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` -> `DelegatedResourceAccountIndexCapsule.addFromAccount`
- Entrypoint: broadcast votes via DelegatedResourceAccountIndexCapsule.addFromAccount
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexCapsule.addFromAccount` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates vote weight through DelegatedResourceAccountIndexCapsule.addFromAccount beyond frozen stake
- Invariant to test: votes counted in DelegatedResourceAccountIndexCapsule.addFromAccount never exceed the voter's frozen stake
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit voting beyond stake asserting rejection
