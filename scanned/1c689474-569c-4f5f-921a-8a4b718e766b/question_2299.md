# Q2299: VoteRewardUtil: call value/token reentrancy

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `VoteRewardUtil.computeReward` in `actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java` — where the attacker reenters VoteRewardUtil.computeReward using a contract they control during a value/TRC10 transfer so balance is read before it is debited — to break the invariant that VoteRewardUtil.computeReward debits before yielding control to callee, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java` -> `VoteRewardUtil.computeReward`
- Entrypoint: reentrant contract exercising VoteRewardUtil.computeReward
- Attacker controls: request/transaction/contract inputs to `VoteRewardUtil.computeReward` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: reenters VoteRewardUtil.computeReward using a contract they control during a value/TRC10 transfer so balance is read before it is debited
- Invariant to test: VoteRewardUtil.computeReward debits before yielding control to callee
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test reentering on transfer asserting single debit
