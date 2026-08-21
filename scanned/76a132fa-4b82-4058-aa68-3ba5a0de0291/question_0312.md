# Q312: DelegatedResourceAccountIndexCapsule: vote weight inflation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexCapsule.addToAccount` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` — where the attacker inflates vote weight through DelegatedResourceAccountIndexCapsule.addToAccount beyond frozen stake — to break the invariant that votes counted in DelegatedResourceAccountIndexCapsule.addToAccount never exceed the voter's frozen stake, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` -> `DelegatedResourceAccountIndexCapsule.addToAccount`
- Entrypoint: broadcast votes via DelegatedResourceAccountIndexCapsule.addToAccount
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexCapsule.addToAccount` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates vote weight through DelegatedResourceAccountIndexCapsule.addToAccount beyond frozen stake
- Invariant to test: votes counted in DelegatedResourceAccountIndexCapsule.addToAccount never exceed the voter's frozen stake
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit voting beyond stake asserting rejection
