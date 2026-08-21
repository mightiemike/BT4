# Q3395: VotesCapsule: market order price/amount overflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VotesCapsule.clearNewVotes` in `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` — where the attacker submits an order via VotesCapsule.clearNewVotes whose price*amount overflows or rounds to seize value from the book — to break the invariant that order math in VotesCapsule.clearNewVotes never overflows or mismatches sell/buy units, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` -> `VotesCapsule.clearNewVotes`
- Entrypoint: broadcast a market order to VotesCapsule.clearNewVotes
- Attacker controls: request/transaction/contract inputs to `VotesCapsule.clearNewVotes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits an order via VotesCapsule.clearNewVotes whose price*amount overflows or rounds to seize value from the book
- Invariant to test: order math in VotesCapsule.clearNewVotes never overflows or mismatches sell/buy units
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with extreme price/amount asserting no overflow
