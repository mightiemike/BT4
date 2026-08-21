# Q439: VotesCapsule: vote weight inflation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VotesCapsule.addOldVotes` in `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` — where the attacker inflates vote weight through VotesCapsule.addOldVotes beyond frozen stake — to break the invariant that votes counted in VotesCapsule.addOldVotes never exceed the voter's frozen stake, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` -> `VotesCapsule.addOldVotes`
- Entrypoint: broadcast votes via VotesCapsule.addOldVotes
- Attacker controls: request/transaction/contract inputs to `VotesCapsule.addOldVotes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates vote weight through VotesCapsule.addOldVotes beyond frozen stake
- Invariant to test: votes counted in VotesCapsule.addOldVotes never exceed the voter's frozen stake
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit voting beyond stake asserting rejection
