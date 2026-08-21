# Q2774: VotesCapsule: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VotesCapsule.getNewVotes` in `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` — where the attacker drives VotesCapsule.getNewVotes usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in VotesCapsule.getNewVotes never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` -> `VotesCapsule.getNewVotes`
- Entrypoint: repeated ops via VotesCapsule.getNewVotes
- Attacker controls: request/transaction/contract inputs to `VotesCapsule.getNewVotes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives VotesCapsule.getNewVotes usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in VotesCapsule.getNewVotes never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
