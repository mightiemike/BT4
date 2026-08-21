# Q375: DelegationStore: market order price/amount overflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegationStore.setWitnessVote` in `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` — where the attacker submits an order via DelegationStore.setWitnessVote whose price*amount overflows or rounds to seize value from the book — to break the invariant that order math in DelegationStore.setWitnessVote never overflows or mismatches sell/buy units, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` -> `DelegationStore.setWitnessVote`
- Entrypoint: broadcast a market order to DelegationStore.setWitnessVote
- Attacker controls: request/transaction/contract inputs to `DelegationStore.setWitnessVote` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits an order via DelegationStore.setWitnessVote whose price*amount overflows or rounds to seize value from the book
- Invariant to test: order math in DelegationStore.setWitnessVote never overflows or mismatches sell/buy units
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with extreme price/amount asserting no overflow
