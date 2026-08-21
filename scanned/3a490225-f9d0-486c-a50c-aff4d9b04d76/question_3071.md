# Q3071: ConsensusDelegate: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ConsensusDelegate.getVotesStore` in `consensus/src/main/java/org/tron/consensus/ConsensusDelegate.java` — where the attacker uses ConsensusDelegate.getVotesStore to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in ConsensusDelegate.getVotesStore preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `consensus/src/main/java/org/tron/consensus/ConsensusDelegate.java` -> `ConsensusDelegate.getVotesStore`
- Entrypoint: broadcast exchange ops via ConsensusDelegate.getVotesStore
- Attacker controls: request/transaction/contract inputs to `ConsensusDelegate.getVotesStore` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses ConsensusDelegate.getVotesStore to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in ConsensusDelegate.getVotesStore preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
