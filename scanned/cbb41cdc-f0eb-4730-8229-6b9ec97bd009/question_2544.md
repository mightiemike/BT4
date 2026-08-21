# Q2544: VotesCapsule: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VotesCapsule.getOldVotes` in `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` — where the attacker repeatedly claims through VotesCapsule.getOldVotes exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in VotesCapsule.getOldVotes, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` -> `VotesCapsule.getOldVotes`
- Entrypoint: many small claims via VotesCapsule.getOldVotes
- Attacker controls: request/transaction/contract inputs to `VotesCapsule.getOldVotes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through VotesCapsule.getOldVotes exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in VotesCapsule.getOldVotes
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
