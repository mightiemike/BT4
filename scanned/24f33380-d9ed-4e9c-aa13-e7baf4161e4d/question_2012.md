# Q2012: DelegatedResourceAccountIndexStore: vote weight inflation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexStore.unDelegateV2` in `chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java` — where the attacker inflates vote weight through DelegatedResourceAccountIndexStore.unDelegateV2 beyond frozen stake — to break the invariant that votes counted in DelegatedResourceAccountIndexStore.unDelegateV2 never exceed the voter's frozen stake, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java` -> `DelegatedResourceAccountIndexStore.unDelegateV2`
- Entrypoint: broadcast votes via DelegatedResourceAccountIndexStore.unDelegateV2
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexStore.unDelegateV2` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates vote weight through DelegatedResourceAccountIndexStore.unDelegateV2 beyond frozen stake
- Invariant to test: votes counted in DelegatedResourceAccountIndexStore.unDelegateV2 never exceed the voter's frozen stake
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit voting beyond stake asserting rejection
