# Q1224: DelegatedResourceAccountIndexCapsule: delegate/undelegate accounting

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexCapsule.createReadableString` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` — where the attacker races delegate and undelegate through DelegatedResourceAccountIndexCapsule.createReadableString so delegated resource is counted twice or freed twice — to break the invariant that delegated resource conserved across concurrent DelegatedResourceAccountIndexCapsule.createReadableString calls, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` -> `DelegatedResourceAccountIndexCapsule.createReadableString`
- Entrypoint: interleave DelegatedResourceAccountIndexCapsule.createReadableString delegate/undelegate
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexCapsule.createReadableString` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: races delegate and undelegate through DelegatedResourceAccountIndexCapsule.createReadableString so delegated resource is counted twice or freed twice
- Invariant to test: delegated resource conserved across concurrent DelegatedResourceAccountIndexCapsule.createReadableString calls
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit sequencing delegate+undelegate asserting conservation
