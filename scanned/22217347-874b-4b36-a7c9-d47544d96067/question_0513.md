# Q513: DelegatedResourceAccountIndexCapsule: delegate/undelegate accounting

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexCapsule.removeFromAccount` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` — where the attacker races delegate and undelegate through DelegatedResourceAccountIndexCapsule.removeFromAccount so delegated resource is counted twice or freed twice — to break the invariant that delegated resource conserved across concurrent DelegatedResourceAccountIndexCapsule.removeFromAccount calls, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` -> `DelegatedResourceAccountIndexCapsule.removeFromAccount`
- Entrypoint: interleave DelegatedResourceAccountIndexCapsule.removeFromAccount delegate/undelegate
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexCapsule.removeFromAccount` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: races delegate and undelegate through DelegatedResourceAccountIndexCapsule.removeFromAccount so delegated resource is counted twice or freed twice
- Invariant to test: delegated resource conserved across concurrent DelegatedResourceAccountIndexCapsule.removeFromAccount calls
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit sequencing delegate+undelegate asserting conservation
