# Q402: DelegationStore: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegationStore.addReward` in `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` — where the attacker drives DelegationStore.addReward usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in DelegationStore.addReward never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` -> `DelegationStore.addReward`
- Entrypoint: repeated ops via DelegationStore.addReward
- Attacker controls: request/transaction/contract inputs to `DelegationStore.addReward` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives DelegationStore.addReward usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in DelegationStore.addReward never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
