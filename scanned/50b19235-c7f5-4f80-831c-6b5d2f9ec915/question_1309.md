# Q1309: VotesCapsule: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VotesCapsule.clearOldVotes` in `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` — where the attacker uses VotesCapsule.clearOldVotes to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in VotesCapsule.clearOldVotes preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` -> `VotesCapsule.clearOldVotes`
- Entrypoint: broadcast exchange ops via VotesCapsule.clearOldVotes
- Attacker controls: request/transaction/contract inputs to `VotesCapsule.clearOldVotes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses VotesCapsule.clearOldVotes to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in VotesCapsule.clearOldVotes preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
