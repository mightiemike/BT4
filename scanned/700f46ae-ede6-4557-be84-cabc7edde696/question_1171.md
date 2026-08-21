# Q1171: DelegatedResourceAccountIndexStore: delegate/undelegate accounting

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexStore.unDelegate` in `chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java` — where the attacker races delegate and undelegate through DelegatedResourceAccountIndexStore.unDelegate so delegated resource is counted twice or freed twice — to break the invariant that delegated resource conserved across concurrent DelegatedResourceAccountIndexStore.unDelegate calls, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java` -> `DelegatedResourceAccountIndexStore.unDelegate`
- Entrypoint: interleave DelegatedResourceAccountIndexStore.unDelegate delegate/undelegate
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexStore.unDelegate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: races delegate and undelegate through DelegatedResourceAccountIndexStore.unDelegate so delegated resource is counted twice or freed twice
- Invariant to test: delegated resource conserved across concurrent DelegatedResourceAccountIndexStore.unDelegate calls
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit sequencing delegate+undelegate asserting conservation
