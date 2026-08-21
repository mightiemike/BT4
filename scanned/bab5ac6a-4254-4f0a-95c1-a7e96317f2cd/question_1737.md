# Q1737: DelegatedResourceAccountIndexCapsule: market order price/amount overflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexCapsule.createReadableString` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` — where the attacker submits an order via DelegatedResourceAccountIndexCapsule.createReadableString whose price*amount overflows or rounds to seize value from the book — to break the invariant that order math in DelegatedResourceAccountIndexCapsule.createReadableString never overflows or mismatches sell/buy units, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` -> `DelegatedResourceAccountIndexCapsule.createReadableString`
- Entrypoint: broadcast a market order to DelegatedResourceAccountIndexCapsule.createReadableString
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexCapsule.createReadableString` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits an order via DelegatedResourceAccountIndexCapsule.createReadableString whose price*amount overflows or rounds to seize value from the book
- Invariant to test: order math in DelegatedResourceAccountIndexCapsule.createReadableString never overflows or mismatches sell/buy units
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with extreme price/amount asserting no overflow
