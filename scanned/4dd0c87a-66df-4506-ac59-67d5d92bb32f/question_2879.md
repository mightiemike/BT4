# Q2879: DelegatedResourceCapsule: delegate/undelegate accounting

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceCapsule.addFrozenBalanceForEnergy` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java` — where the attacker races delegate and undelegate through DelegatedResourceCapsule.addFrozenBalanceForEnergy so delegated resource is counted twice or freed twice — to break the invariant that delegated resource conserved across concurrent DelegatedResourceCapsule.addFrozenBalanceForEnergy calls, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java` -> `DelegatedResourceCapsule.addFrozenBalanceForEnergy`
- Entrypoint: interleave DelegatedResourceCapsule.addFrozenBalanceForEnergy delegate/undelegate
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceCapsule.addFrozenBalanceForEnergy` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: races delegate and undelegate through DelegatedResourceCapsule.addFrozenBalanceForEnergy so delegated resource is counted twice or freed twice
- Invariant to test: delegated resource conserved across concurrent DelegatedResourceCapsule.addFrozenBalanceForEnergy calls
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit sequencing delegate+undelegate asserting conservation
