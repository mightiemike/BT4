# Q2545: ConsensusDelegate: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ConsensusDelegate.calculateFilledSlotsCount` in `consensus/src/main/java/org/tron/consensus/ConsensusDelegate.java` — where the attacker drives ConsensusDelegate.calculateFilledSlotsCount usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in ConsensusDelegate.calculateFilledSlotsCount never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `consensus/src/main/java/org/tron/consensus/ConsensusDelegate.java` -> `ConsensusDelegate.calculateFilledSlotsCount`
- Entrypoint: repeated ops via ConsensusDelegate.calculateFilledSlotsCount
- Attacker controls: request/transaction/contract inputs to `ConsensusDelegate.calculateFilledSlotsCount` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives ConsensusDelegate.calculateFilledSlotsCount usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in ConsensusDelegate.calculateFilledSlotsCount never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
