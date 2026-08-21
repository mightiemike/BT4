# Q2027: DelegatedResourceStore: vote weight inflation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceStore.unLockExpireResource` in `chainbase/src/main/java/org/tron/core/store/DelegatedResourceStore.java` — where the attacker inflates vote weight through DelegatedResourceStore.unLockExpireResource beyond frozen stake — to break the invariant that votes counted in DelegatedResourceStore.unLockExpireResource never exceed the voter's frozen stake, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegatedResourceStore.java` -> `DelegatedResourceStore.unLockExpireResource`
- Entrypoint: broadcast votes via DelegatedResourceStore.unLockExpireResource
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceStore.unLockExpireResource` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates vote weight through DelegatedResourceStore.unLockExpireResource beyond frozen stake
- Invariant to test: votes counted in DelegatedResourceStore.unLockExpireResource never exceed the voter's frozen stake
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit voting beyond stake asserting rejection
