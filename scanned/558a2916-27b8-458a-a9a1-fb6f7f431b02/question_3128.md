# Q3128: DelegationStore: vote weight inflation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegationStore.getReward` in `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` — where the attacker inflates vote weight through DelegationStore.getReward beyond frozen stake — to break the invariant that votes counted in DelegationStore.getReward never exceed the voter's frozen stake, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` -> `DelegationStore.getReward`
- Entrypoint: broadcast votes via DelegationStore.getReward
- Attacker controls: request/transaction/contract inputs to `DelegationStore.getReward` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates vote weight through DelegationStore.getReward beyond frozen stake
- Invariant to test: votes counted in DelegationStore.getReward never exceed the voter's frozen stake
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit voting beyond stake asserting rejection
