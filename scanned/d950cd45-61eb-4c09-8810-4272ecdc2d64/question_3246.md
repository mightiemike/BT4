# Q3246: DelegatedResourceStore: delegate/undelegate accounting

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceStore.unLockExpireResource` in `chainbase/src/main/java/org/tron/core/store/DelegatedResourceStore.java` — where the attacker races delegate and undelegate through DelegatedResourceStore.unLockExpireResource so delegated resource is counted twice or freed twice — to break the invariant that delegated resource conserved across concurrent DelegatedResourceStore.unLockExpireResource calls, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegatedResourceStore.java` -> `DelegatedResourceStore.unLockExpireResource`
- Entrypoint: interleave DelegatedResourceStore.unLockExpireResource delegate/undelegate
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceStore.unLockExpireResource` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: races delegate and undelegate through DelegatedResourceStore.unLockExpireResource so delegated resource is counted twice or freed twice
- Invariant to test: delegated resource conserved across concurrent DelegatedResourceStore.unLockExpireResource calls
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit sequencing delegate+undelegate asserting conservation
