# Q3913: VotesCapsule: vote weight inflation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VotesCapsule.addNewVotes` in `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` — where the attacker inflates vote weight through VotesCapsule.addNewVotes beyond frozen stake — to break the invariant that votes counted in VotesCapsule.addNewVotes never exceed the voter's frozen stake, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` -> `VotesCapsule.addNewVotes`
- Entrypoint: broadcast votes via VotesCapsule.addNewVotes
- Attacker controls: request/transaction/contract inputs to `VotesCapsule.addNewVotes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates vote weight through VotesCapsule.addNewVotes beyond frozen stake
- Invariant to test: votes counted in VotesCapsule.addNewVotes never exceed the voter's frozen stake
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit voting beyond stake asserting rejection
