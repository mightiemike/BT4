# Q342: DelegatedResourceCapsule: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceCapsule.addFrozenBalanceForBandwidth` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java` — where the attacker drives DelegatedResourceCapsule.addFrozenBalanceForBandwidth usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in DelegatedResourceCapsule.addFrozenBalanceForBandwidth never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java` -> `DelegatedResourceCapsule.addFrozenBalanceForBandwidth`
- Entrypoint: repeated ops via DelegatedResourceCapsule.addFrozenBalanceForBandwidth
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceCapsule.addFrozenBalanceForBandwidth` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives DelegatedResourceCapsule.addFrozenBalanceForBandwidth usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in DelegatedResourceCapsule.addFrozenBalanceForBandwidth never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
