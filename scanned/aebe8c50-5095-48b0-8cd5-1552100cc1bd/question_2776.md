# Q2776: VoteRewardUtil: stack/depth bound bypass

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `VoteRewardUtil.adjustAllowance` in `actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java` — where the attacker recurses or grows stack via VoteRewardUtil.adjustAllowance past the depth/size bound without proportional cost — to break the invariant that VoteRewardUtil.adjustAllowance enforces call depth and stack size before work, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java` -> `VoteRewardUtil.adjustAllowance`
- Entrypoint: deeply nested call reaching VoteRewardUtil.adjustAllowance
- Attacker controls: request/transaction/contract inputs to `VoteRewardUtil.adjustAllowance` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: recurses or grows stack via VoteRewardUtil.adjustAllowance past the depth/size bound without proportional cost
- Invariant to test: VoteRewardUtil.adjustAllowance enforces call depth and stack size before work
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test at depth boundary asserting revert not hang
