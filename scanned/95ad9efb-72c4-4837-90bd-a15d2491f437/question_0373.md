# Q373: DelegationStore: delegate/undelegate accounting

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegationStore.getReward` in `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` — where the attacker races delegate and undelegate through DelegationStore.getReward so delegated resource is counted twice or freed twice — to break the invariant that delegated resource conserved across concurrent DelegationStore.getReward calls, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` -> `DelegationStore.getReward`
- Entrypoint: interleave DelegationStore.getReward delegate/undelegate
- Attacker controls: request/transaction/contract inputs to `DelegationStore.getReward` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: races delegate and undelegate through DelegationStore.getReward so delegated resource is counted twice or freed twice
- Invariant to test: delegated resource conserved across concurrent DelegationStore.getReward calls
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit sequencing delegate+undelegate asserting conservation
