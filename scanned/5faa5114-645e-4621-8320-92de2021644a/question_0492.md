# Q492: DelegationStore: vote weight inflation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegationStore.buildRewardKey` in `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` — where the attacker inflates vote weight through DelegationStore.buildRewardKey beyond frozen stake — to break the invariant that votes counted in DelegationStore.buildRewardKey never exceed the voter's frozen stake, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` -> `DelegationStore.buildRewardKey`
- Entrypoint: broadcast votes via DelegationStore.buildRewardKey
- Attacker controls: request/transaction/contract inputs to `DelegationStore.buildRewardKey` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates vote weight through DelegationStore.buildRewardKey beyond frozen stake
- Invariant to test: votes counted in DelegationStore.buildRewardKey never exceed the voter's frozen stake
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit voting beyond stake asserting rejection
