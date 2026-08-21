# Q366: DelegatedResourceCapsule: vote weight inflation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceCapsule.addFrozenBalanceForEnergy` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java` — where the attacker inflates vote weight through DelegatedResourceCapsule.addFrozenBalanceForEnergy beyond frozen stake — to break the invariant that votes counted in DelegatedResourceCapsule.addFrozenBalanceForEnergy never exceed the voter's frozen stake, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java` -> `DelegatedResourceCapsule.addFrozenBalanceForEnergy`
- Entrypoint: broadcast votes via DelegatedResourceCapsule.addFrozenBalanceForEnergy
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceCapsule.addFrozenBalanceForEnergy` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates vote weight through DelegatedResourceCapsule.addFrozenBalanceForEnergy beyond frozen stake
- Invariant to test: votes counted in DelegatedResourceCapsule.addFrozenBalanceForEnergy never exceed the voter's frozen stake
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit voting beyond stake asserting rejection
