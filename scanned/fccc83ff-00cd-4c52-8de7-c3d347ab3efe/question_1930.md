# Q1930: VotesCapsule: delegate/undelegate accounting

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VotesCapsule.setOldVote` in `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` — where the attacker races delegate and undelegate through VotesCapsule.setOldVote so delegated resource is counted twice or freed twice — to break the invariant that delegated resource conserved across concurrent VotesCapsule.setOldVote calls, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` -> `VotesCapsule.setOldVote`
- Entrypoint: interleave VotesCapsule.setOldVote delegate/undelegate
- Attacker controls: request/transaction/contract inputs to `VotesCapsule.setOldVote` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: races delegate and undelegate through VotesCapsule.setOldVote so delegated resource is counted twice or freed twice
- Invariant to test: delegated resource conserved across concurrent VotesCapsule.setOldVote calls
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit sequencing delegate+undelegate asserting conservation
