# Q1926: VotesCapsule: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VotesCapsule.addNewVotes` in `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` — where the attacker drives VotesCapsule.addNewVotes usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in VotesCapsule.addNewVotes never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` -> `VotesCapsule.addNewVotes`
- Entrypoint: repeated ops via VotesCapsule.addNewVotes
- Attacker controls: request/transaction/contract inputs to `VotesCapsule.addNewVotes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives VotesCapsule.addNewVotes usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in VotesCapsule.addNewVotes never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
