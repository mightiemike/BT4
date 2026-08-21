# Q118: DelegatedResourceCapsule: vote weight inflation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceCapsule.addFrozenBalanceForBandwidth` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java` — where the attacker inflates vote weight through DelegatedResourceCapsule.addFrozenBalanceForBandwidth beyond frozen stake — to break the invariant that votes counted in DelegatedResourceCapsule.addFrozenBalanceForBandwidth never exceed the voter's frozen stake, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java` -> `DelegatedResourceCapsule.addFrozenBalanceForBandwidth`
- Entrypoint: broadcast votes via DelegatedResourceCapsule.addFrozenBalanceForBandwidth
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceCapsule.addFrozenBalanceForBandwidth` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates vote weight through DelegatedResourceCapsule.addFrozenBalanceForBandwidth beyond frozen stake
- Invariant to test: votes counted in DelegatedResourceCapsule.addFrozenBalanceForBandwidth never exceed the voter's frozen stake
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit voting beyond stake asserting rejection
