# Q2790: DelegationStore: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegationStore.setAccountVote` in `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` — where the attacker drives DelegationStore.setAccountVote usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in DelegationStore.setAccountVote never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` -> `DelegationStore.setAccountVote`
- Entrypoint: repeated ops via DelegationStore.setAccountVote
- Attacker controls: request/transaction/contract inputs to `DelegationStore.setAccountVote` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives DelegationStore.setAccountVote usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in DelegationStore.setAccountVote never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
