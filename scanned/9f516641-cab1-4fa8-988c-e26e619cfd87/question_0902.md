# Q902: DelegatedResourceStore: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceStore.unLockExpireResource` in `chainbase/src/main/java/org/tron/core/store/DelegatedResourceStore.java` — where the attacker repeatedly claims through DelegatedResourceStore.unLockExpireResource exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in DelegatedResourceStore.unLockExpireResource, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegatedResourceStore.java` -> `DelegatedResourceStore.unLockExpireResource`
- Entrypoint: many small claims via DelegatedResourceStore.unLockExpireResource
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceStore.unLockExpireResource` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through DelegatedResourceStore.unLockExpireResource exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in DelegatedResourceStore.unLockExpireResource
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
