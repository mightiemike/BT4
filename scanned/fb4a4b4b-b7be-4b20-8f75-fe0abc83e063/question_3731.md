# Q3731: DelegatedResourceAccountIndexStore: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexStore.delegate` in `chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java` — where the attacker uses DelegatedResourceAccountIndexStore.delegate to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in DelegatedResourceAccountIndexStore.delegate preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java` -> `DelegatedResourceAccountIndexStore.delegate`
- Entrypoint: broadcast exchange ops via DelegatedResourceAccountIndexStore.delegate
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexStore.delegate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses DelegatedResourceAccountIndexStore.delegate to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in DelegatedResourceAccountIndexStore.delegate preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
