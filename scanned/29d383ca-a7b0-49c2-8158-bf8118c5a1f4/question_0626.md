# Q626: DelegationStore: market order price/amount overflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegationStore.getReward` in `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` — where the attacker submits an order via DelegationStore.getReward whose price*amount overflows or rounds to seize value from the book — to break the invariant that order math in DelegationStore.getReward never overflows or mismatches sell/buy units, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` -> `DelegationStore.getReward`
- Entrypoint: broadcast a market order to DelegationStore.getReward
- Attacker controls: request/transaction/contract inputs to `DelegationStore.getReward` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits an order via DelegationStore.getReward whose price*amount overflows or rounds to seize value from the book
- Invariant to test: order math in DelegationStore.getReward never overflows or mismatches sell/buy units
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with extreme price/amount asserting no overflow
