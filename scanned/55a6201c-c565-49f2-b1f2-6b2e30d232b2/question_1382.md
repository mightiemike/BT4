# Q1382: DelegatedResourceStore: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceStore.unLockExpireResource` in `chainbase/src/main/java/org/tron/core/store/DelegatedResourceStore.java` — where the attacker uses DelegatedResourceStore.unLockExpireResource to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in DelegatedResourceStore.unLockExpireResource preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegatedResourceStore.java` -> `DelegatedResourceStore.unLockExpireResource`
- Entrypoint: broadcast exchange ops via DelegatedResourceStore.unLockExpireResource
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceStore.unLockExpireResource` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses DelegatedResourceStore.unLockExpireResource to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in DelegatedResourceStore.unLockExpireResource preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
