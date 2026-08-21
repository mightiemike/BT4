# Q1104: DelegatedResourceCapsule: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceCapsule.addFrozenBalanceForEnergy` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java` — where the attacker drives DelegatedResourceCapsule.addFrozenBalanceForEnergy usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in DelegatedResourceCapsule.addFrozenBalanceForEnergy never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java` -> `DelegatedResourceCapsule.addFrozenBalanceForEnergy`
- Entrypoint: repeated ops via DelegatedResourceCapsule.addFrozenBalanceForEnergy
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceCapsule.addFrozenBalanceForEnergy` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives DelegatedResourceCapsule.addFrozenBalanceForEnergy usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in DelegatedResourceCapsule.addFrozenBalanceForEnergy never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
