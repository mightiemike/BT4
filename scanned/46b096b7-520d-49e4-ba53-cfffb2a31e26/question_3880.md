# Q3880: VoteRewardUtil: call value/token reentrancy

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `VoteRewardUtil.adjustAllowance` in `actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java` — where the attacker reenters VoteRewardUtil.adjustAllowance using a contract they control during a value/TRC10 transfer so balance is read before it is debited — to break the invariant that VoteRewardUtil.adjustAllowance debits before yielding control to callee, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java` -> `VoteRewardUtil.adjustAllowance`
- Entrypoint: reentrant contract exercising VoteRewardUtil.adjustAllowance
- Attacker controls: request/transaction/contract inputs to `VoteRewardUtil.adjustAllowance` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: reenters VoteRewardUtil.adjustAllowance using a contract they control during a value/TRC10 transfer so balance is read before it is debited
- Invariant to test: VoteRewardUtil.adjustAllowance debits before yielding control to callee
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test reentering on transfer asserting single debit
