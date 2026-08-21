# Q1108: VoteRewardUtil: non-deterministic result

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `VoteRewardUtil.computeReward` in `actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java` — where the attacker finds an input to VoteRewardUtil.computeReward whose output depends on JDK/platform/iteration order, diverging node state — to break the invariant that VoteRewardUtil.computeReward is deterministic across JDK and node, leading to: Consensus divergence (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java` -> `VoteRewardUtil.computeReward`
- Entrypoint: contract exercising VoteRewardUtil.computeReward edge input
- Attacker controls: request/transaction/contract inputs to `VoteRewardUtil.computeReward` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to VoteRewardUtil.computeReward whose output depends on JDK/platform/iteration order, diverging node state
- Invariant to test: VoteRewardUtil.computeReward is deterministic across JDK and node
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential run across JDK8/17 asserting identical result
