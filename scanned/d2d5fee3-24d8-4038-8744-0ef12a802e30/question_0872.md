# Q872: DelegationStore: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegationStore.getAccountVote` in `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` — where the attacker drives DelegationStore.getAccountVote usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in DelegationStore.getAccountVote never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` -> `DelegationStore.getAccountVote`
- Entrypoint: repeated ops via DelegationStore.getAccountVote
- Attacker controls: request/transaction/contract inputs to `DelegationStore.getAccountVote` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives DelegationStore.getAccountVote usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in DelegationStore.getAccountVote never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
