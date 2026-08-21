# Q1606: VotesCapsule: delegate/undelegate accounting

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VotesCapsule.setOldVotes` in `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` — where the attacker races delegate and undelegate through VotesCapsule.setOldVotes so delegated resource is counted twice or freed twice — to break the invariant that delegated resource conserved across concurrent VotesCapsule.setOldVotes calls, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` -> `VotesCapsule.setOldVotes`
- Entrypoint: interleave VotesCapsule.setOldVotes delegate/undelegate
- Attacker controls: request/transaction/contract inputs to `VotesCapsule.setOldVotes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: races delegate and undelegate through VotesCapsule.setOldVotes so delegated resource is counted twice or freed twice
- Invariant to test: delegated resource conserved across concurrent VotesCapsule.setOldVotes calls
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit sequencing delegate+undelegate asserting conservation
