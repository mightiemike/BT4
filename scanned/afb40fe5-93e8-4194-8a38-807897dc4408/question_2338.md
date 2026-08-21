# Q2338: ResourceProcessor: market order price/amount overflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ResourceProcessor.unDelegateIncreaseV2` in `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` — where the attacker submits an order via ResourceProcessor.unDelegateIncreaseV2 whose price*amount overflows or rounds to seize value from the book — to break the invariant that order math in ResourceProcessor.unDelegateIncreaseV2 never overflows or mismatches sell/buy units, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` -> `ResourceProcessor.unDelegateIncreaseV2`
- Entrypoint: broadcast a market order to ResourceProcessor.unDelegateIncreaseV2
- Attacker controls: request/transaction/contract inputs to `ResourceProcessor.unDelegateIncreaseV2` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits an order via ResourceProcessor.unDelegateIncreaseV2 whose price*amount overflows or rounds to seize value from the book
- Invariant to test: order math in ResourceProcessor.unDelegateIncreaseV2 never overflows or mismatches sell/buy units
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with extreme price/amount asserting no overflow
