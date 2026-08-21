# Q3613: DelegatedResourceCapsule: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceCapsule.createDbKey` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java` — where the attacker repeatedly claims through DelegatedResourceCapsule.createDbKey exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in DelegatedResourceCapsule.createDbKey, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java` -> `DelegatedResourceCapsule.createDbKey`
- Entrypoint: many small claims via DelegatedResourceCapsule.createDbKey
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceCapsule.createDbKey` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through DelegatedResourceCapsule.createDbKey exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in DelegatedResourceCapsule.createDbKey
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
