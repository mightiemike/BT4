# Q1399: DelegatedResourceAccountIndexStore: vote weight inflation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexStore.unDelegate` in `chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java` — where the attacker inflates vote weight through DelegatedResourceAccountIndexStore.unDelegate beyond frozen stake — to break the invariant that votes counted in DelegatedResourceAccountIndexStore.unDelegate never exceed the voter's frozen stake, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java` -> `DelegatedResourceAccountIndexStore.unDelegate`
- Entrypoint: broadcast votes via DelegatedResourceAccountIndexStore.unDelegate
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexStore.unDelegate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates vote weight through DelegatedResourceAccountIndexStore.unDelegate beyond frozen stake
- Invariant to test: votes counted in DelegatedResourceAccountIndexStore.unDelegate never exceed the voter's frozen stake
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit voting beyond stake asserting rejection
