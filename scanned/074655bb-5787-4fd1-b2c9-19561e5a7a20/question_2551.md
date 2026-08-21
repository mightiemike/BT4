# Q2551: DelegationStore: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegationStore.setAccountVote` in `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` — where the attacker repeatedly claims through DelegationStore.setAccountVote exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in DelegationStore.setAccountVote, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` -> `DelegationStore.setAccountVote`
- Entrypoint: many small claims via DelegationStore.setAccountVote
- Attacker controls: request/transaction/contract inputs to `DelegationStore.setAccountVote` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through DelegationStore.setAccountVote exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in DelegationStore.setAccountVote
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
