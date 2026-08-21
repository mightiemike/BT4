# Q2248: DelegationStore: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegationStore.getReward` in `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` — where the attacker repeatedly claims through DelegationStore.getReward exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in DelegationStore.getReward, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` -> `DelegationStore.getReward`
- Entrypoint: many small claims via DelegationStore.getReward
- Attacker controls: request/transaction/contract inputs to `DelegationStore.getReward` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through DelegationStore.getReward exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in DelegationStore.getReward
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
