# Q2572: DelegatedResourceAccountIndexStore: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexStore.unDelegate` in `chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java` — where the attacker repeatedly claims through DelegatedResourceAccountIndexStore.unDelegate exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in DelegatedResourceAccountIndexStore.unDelegate, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java` -> `DelegatedResourceAccountIndexStore.unDelegate`
- Entrypoint: many small claims via DelegatedResourceAccountIndexStore.unDelegate
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexStore.unDelegate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through DelegatedResourceAccountIndexStore.unDelegate exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in DelegatedResourceAccountIndexStore.unDelegate
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
