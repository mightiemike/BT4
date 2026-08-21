# Q3868: DelegationStore: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegationStore.buildRewardKey` in `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` — where the attacker drives DelegationStore.buildRewardKey usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in DelegationStore.buildRewardKey never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` -> `DelegationStore.buildRewardKey`
- Entrypoint: repeated ops via DelegationStore.buildRewardKey
- Attacker controls: request/transaction/contract inputs to `DelegationStore.buildRewardKey` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives DelegationStore.buildRewardKey usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in DelegationStore.buildRewardKey never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
