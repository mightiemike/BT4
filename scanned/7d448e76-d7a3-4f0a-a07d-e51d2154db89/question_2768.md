# Q2768: VotesCapsule: vote weight inflation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VotesCapsule.setOldVotes` in `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` — where the attacker inflates vote weight through VotesCapsule.setOldVotes beyond frozen stake — to break the invariant that votes counted in VotesCapsule.setOldVotes never exceed the voter's frozen stake, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` -> `VotesCapsule.setOldVotes`
- Entrypoint: broadcast votes via VotesCapsule.setOldVotes
- Attacker controls: request/transaction/contract inputs to `VotesCapsule.setOldVotes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates vote weight through VotesCapsule.setOldVotes beyond frozen stake
- Invariant to test: votes counted in VotesCapsule.setOldVotes never exceed the voter's frozen stake
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit voting beyond stake asserting rejection
