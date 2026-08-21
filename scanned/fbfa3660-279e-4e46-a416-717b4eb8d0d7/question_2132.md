# Q2132: VotesCapsule: delegate/undelegate accounting

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VotesCapsule.getOldVotes` in `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` — where the attacker races delegate and undelegate through VotesCapsule.getOldVotes so delegated resource is counted twice or freed twice — to break the invariant that delegated resource conserved across concurrent VotesCapsule.getOldVotes calls, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` -> `VotesCapsule.getOldVotes`
- Entrypoint: interleave VotesCapsule.getOldVotes delegate/undelegate
- Attacker controls: request/transaction/contract inputs to `VotesCapsule.getOldVotes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: races delegate and undelegate through VotesCapsule.getOldVotes so delegated resource is counted twice or freed twice
- Invariant to test: delegated resource conserved across concurrent VotesCapsule.getOldVotes calls
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit sequencing delegate+undelegate asserting conservation
