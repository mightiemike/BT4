# Q222: VoteRewardUtil: create2/address collision

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `VoteRewardUtil.computeReward` in `actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java` — where the attacker uses VoteRewardUtil.computeReward to deploy to a predicted address colliding with existing code/state — to break the invariant that address derivation in VoteRewardUtil.computeReward cannot overwrite existing contract state, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java` -> `VoteRewardUtil.computeReward`
- Entrypoint: CREATE/CREATE2 via VoteRewardUtil.computeReward
- Attacker controls: request/transaction/contract inputs to `VoteRewardUtil.computeReward` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses VoteRewardUtil.computeReward to deploy to a predicted address colliding with existing code/state
- Invariant to test: address derivation in VoteRewardUtil.computeReward cannot overwrite existing contract state
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test colliding deploy asserting no overwrite
