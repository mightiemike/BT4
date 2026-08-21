# Q135: DelegatedResourceAccountIndexCapsule: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexCapsule.addFromAccount` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` — where the attacker repeatedly claims through DelegatedResourceAccountIndexCapsule.addFromAccount exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in DelegatedResourceAccountIndexCapsule.addFromAccount, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` -> `DelegatedResourceAccountIndexCapsule.addFromAccount`
- Entrypoint: many small claims via DelegatedResourceAccountIndexCapsule.addFromAccount
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexCapsule.addFromAccount` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through DelegatedResourceAccountIndexCapsule.addFromAccount exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in DelegatedResourceAccountIndexCapsule.addFromAccount
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
