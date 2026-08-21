# Q1325: VotesCapsule: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VotesCapsule.setOldVote` in `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` — where the attacker uses VotesCapsule.setOldVote to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in VotesCapsule.setOldVote preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` -> `VotesCapsule.setOldVote`
- Entrypoint: broadcast exchange ops via VotesCapsule.setOldVote
- Attacker controls: request/transaction/contract inputs to `VotesCapsule.setOldVote` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses VotesCapsule.setOldVote to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in VotesCapsule.setOldVote preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
