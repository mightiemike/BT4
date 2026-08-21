# Q3266: DelegatedResourceAccountIndexCapsule: market order price/amount overflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexCapsule.removeToAccount` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` — where the attacker submits an order via DelegatedResourceAccountIndexCapsule.removeToAccount whose price*amount overflows or rounds to seize value from the book — to break the invariant that order math in DelegatedResourceAccountIndexCapsule.removeToAccount never overflows or mismatches sell/buy units, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` -> `DelegatedResourceAccountIndexCapsule.removeToAccount`
- Entrypoint: broadcast a market order to DelegatedResourceAccountIndexCapsule.removeToAccount
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexCapsule.removeToAccount` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits an order via DelegatedResourceAccountIndexCapsule.removeToAccount whose price*amount overflows or rounds to seize value from the book
- Invariant to test: order math in DelegatedResourceAccountIndexCapsule.removeToAccount never overflows or mismatches sell/buy units
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with extreme price/amount asserting no overflow
