# Q2802: VotesCapsule: delegate/undelegate accounting

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VotesCapsule.clearNewVotes` in `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` — where the attacker races delegate and undelegate through VotesCapsule.clearNewVotes so delegated resource is counted twice or freed twice — to break the invariant that delegated resource conserved across concurrent VotesCapsule.clearNewVotes calls, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` -> `VotesCapsule.clearNewVotes`
- Entrypoint: interleave VotesCapsule.clearNewVotes delegate/undelegate
- Attacker controls: request/transaction/contract inputs to `VotesCapsule.clearNewVotes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: races delegate and undelegate through VotesCapsule.clearNewVotes so delegated resource is counted twice or freed twice
- Invariant to test: delegated resource conserved across concurrent VotesCapsule.clearNewVotes calls
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit sequencing delegate+undelegate asserting conservation
