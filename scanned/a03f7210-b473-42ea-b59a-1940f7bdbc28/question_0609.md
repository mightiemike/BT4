# Q609: ConsensusDelegate: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ConsensusDelegate.calculateFilledSlotsCount` in `consensus/src/main/java/org/tron/consensus/ConsensusDelegate.java` — where the attacker repeatedly claims through ConsensusDelegate.calculateFilledSlotsCount exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in ConsensusDelegate.calculateFilledSlotsCount, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `consensus/src/main/java/org/tron/consensus/ConsensusDelegate.java` -> `ConsensusDelegate.calculateFilledSlotsCount`
- Entrypoint: many small claims via ConsensusDelegate.calculateFilledSlotsCount
- Attacker controls: request/transaction/contract inputs to `ConsensusDelegate.calculateFilledSlotsCount` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through ConsensusDelegate.calculateFilledSlotsCount exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in ConsensusDelegate.calculateFilledSlotsCount
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
