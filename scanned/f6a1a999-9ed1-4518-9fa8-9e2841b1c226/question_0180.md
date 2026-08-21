# Q180: DelegatedResourceAccountIndexStore: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexStore.delegateV2` in `chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java` — where the attacker uses DelegatedResourceAccountIndexStore.delegateV2 to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in DelegatedResourceAccountIndexStore.delegateV2 preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java` -> `DelegatedResourceAccountIndexStore.delegateV2`
- Entrypoint: broadcast exchange ops via DelegatedResourceAccountIndexStore.delegateV2
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexStore.delegateV2` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses DelegatedResourceAccountIndexStore.delegateV2 to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in DelegatedResourceAccountIndexStore.delegateV2 preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
