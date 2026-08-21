# Q2821: VoteRewardUtil: energy metering mismatch

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `VoteRewardUtil.withdrawReward` in `actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java` — where the attacker crafts a sequence reaching VoteRewardUtil.withdrawReward where charged energy diverges from actual CPU/memory work, or is charged after the work — to break the invariant that energy is charged before or exactly in step with the work in VoteRewardUtil.withdrawReward, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java` -> `VoteRewardUtil.withdrawReward`
- Entrypoint: deploy/trigger a contract exercising VoteRewardUtil.withdrawReward
- Attacker controls: request/transaction/contract inputs to `VoteRewardUtil.withdrawReward` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a sequence reaching VoteRewardUtil.withdrawReward where charged energy diverges from actual CPU/memory work, or is charged after the work
- Invariant to test: energy is charged before or exactly in step with the work in VoteRewardUtil.withdrawReward
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test comparing charged energy to executed steps
