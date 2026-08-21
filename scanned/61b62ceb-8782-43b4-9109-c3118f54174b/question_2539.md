# Q2539: DelegatedResourceAccountIndexCapsule: market order price/amount overflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexCapsule.addToAccount` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` — where the attacker submits an order via DelegatedResourceAccountIndexCapsule.addToAccount whose price*amount overflows or rounds to seize value from the book — to break the invariant that order math in DelegatedResourceAccountIndexCapsule.addToAccount never overflows or mismatches sell/buy units, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` -> `DelegatedResourceAccountIndexCapsule.addToAccount`
- Entrypoint: broadcast a market order to DelegatedResourceAccountIndexCapsule.addToAccount
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexCapsule.addToAccount` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits an order via DelegatedResourceAccountIndexCapsule.addToAccount whose price*amount overflows or rounds to seize value from the book
- Invariant to test: order math in DelegatedResourceAccountIndexCapsule.addToAccount never overflows or mismatches sell/buy units
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with extreme price/amount asserting no overflow
