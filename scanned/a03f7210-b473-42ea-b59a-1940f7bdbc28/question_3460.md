# Q3460: VoteRewardUtil: create2/address collision

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `VoteRewardUtil.withdrawReward` in `actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java` — where the attacker uses VoteRewardUtil.withdrawReward to deploy to a predicted address colliding with existing code/state — to break the invariant that address derivation in VoteRewardUtil.withdrawReward cannot overwrite existing contract state, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java` -> `VoteRewardUtil.withdrawReward`
- Entrypoint: CREATE/CREATE2 via VoteRewardUtil.withdrawReward
- Attacker controls: request/transaction/contract inputs to `VoteRewardUtil.withdrawReward` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses VoteRewardUtil.withdrawReward to deploy to a predicted address colliding with existing code/state
- Invariant to test: address derivation in VoteRewardUtil.withdrawReward cannot overwrite existing contract state
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test colliding deploy asserting no overwrite
