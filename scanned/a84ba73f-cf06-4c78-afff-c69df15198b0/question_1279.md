# Q1279: VotesCapsule: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VotesCapsule.getNewVotes` in `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` — where the attacker repeatedly claims through VotesCapsule.getNewVotes exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in VotesCapsule.getNewVotes, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` -> `VotesCapsule.getNewVotes`
- Entrypoint: many small claims via VotesCapsule.getNewVotes
- Attacker controls: request/transaction/contract inputs to `VotesCapsule.getNewVotes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through VotesCapsule.getNewVotes exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in VotesCapsule.getNewVotes
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
