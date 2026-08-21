# Q248: VotesCapsule: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VotesCapsule.addAllNewVotes` in `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` — where the attacker drives VotesCapsule.addAllNewVotes usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in VotesCapsule.addAllNewVotes never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` -> `VotesCapsule.addAllNewVotes`
- Entrypoint: repeated ops via VotesCapsule.addAllNewVotes
- Attacker controls: request/transaction/contract inputs to `VotesCapsule.addAllNewVotes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives VotesCapsule.addAllNewVotes usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in VotesCapsule.addAllNewVotes never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
