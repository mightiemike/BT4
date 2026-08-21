# Q3807: MUtil: call value/token reentrancy

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `MUtil.transferToken` in `actuator/src/main/java/org/tron/core/vm/utils/MUtil.java` — where the attacker reenters MUtil.transferToken using a contract they control during a value/TRC10 transfer so balance is read before it is debited — to break the invariant that MUtil.transferToken debits before yielding control to callee, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/utils/MUtil.java` -> `MUtil.transferToken`
- Entrypoint: reentrant contract exercising MUtil.transferToken
- Attacker controls: request/transaction/contract inputs to `MUtil.transferToken` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: reenters MUtil.transferToken using a contract they control during a value/TRC10 transfer so balance is read before it is debited
- Invariant to test: MUtil.transferToken debits before yielding control to callee
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test reentering on transfer asserting single debit
