# Q990: ConsensusDelegate: market order price/amount overflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ConsensusDelegate.getVotesStore` in `consensus/src/main/java/org/tron/consensus/ConsensusDelegate.java` — where the attacker submits an order via ConsensusDelegate.getVotesStore whose price*amount overflows or rounds to seize value from the book — to break the invariant that order math in ConsensusDelegate.getVotesStore never overflows or mismatches sell/buy units, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `consensus/src/main/java/org/tron/consensus/ConsensusDelegate.java` -> `ConsensusDelegate.getVotesStore`
- Entrypoint: broadcast a market order to ConsensusDelegate.getVotesStore
- Attacker controls: request/transaction/contract inputs to `ConsensusDelegate.getVotesStore` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits an order via ConsensusDelegate.getVotesStore whose price*amount overflows or rounds to seize value from the book
- Invariant to test: order math in ConsensusDelegate.getVotesStore never overflows or mismatches sell/buy units
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with extreme price/amount asserting no overflow
