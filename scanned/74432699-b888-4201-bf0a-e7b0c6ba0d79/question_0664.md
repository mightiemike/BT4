# Q664: VotesCapsule: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VotesCapsule.clearNewVotes` in `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` — where the attacker repeatedly claims through VotesCapsule.clearNewVotes exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in VotesCapsule.clearNewVotes, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` -> `VotesCapsule.clearNewVotes`
- Entrypoint: many small claims via VotesCapsule.clearNewVotes
- Attacker controls: request/transaction/contract inputs to `VotesCapsule.clearNewVotes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through VotesCapsule.clearNewVotes exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in VotesCapsule.clearNewVotes
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
