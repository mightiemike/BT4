# Q3929: ConsensusDelegate: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ConsensusDelegate.getVotesStore` in `consensus/src/main/java/org/tron/consensus/ConsensusDelegate.java` — where the attacker drives ConsensusDelegate.getVotesStore usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in ConsensusDelegate.getVotesStore never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `consensus/src/main/java/org/tron/consensus/ConsensusDelegate.java` -> `ConsensusDelegate.getVotesStore`
- Entrypoint: repeated ops via ConsensusDelegate.getVotesStore
- Attacker controls: request/transaction/contract inputs to `ConsensusDelegate.getVotesStore` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives ConsensusDelegate.getVotesStore usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in ConsensusDelegate.getVotesStore never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
