# Q571: FreezeV2Util: call value/token reentrancy

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `FreezeV2Util.queryFrozenBalanceUsage` in `actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java` — where the attacker reenters FreezeV2Util.queryFrozenBalanceUsage using a contract they control during a value/TRC10 transfer so balance is read before it is debited — to break the invariant that FreezeV2Util.queryFrozenBalanceUsage debits before yielding control to callee, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java` -> `FreezeV2Util.queryFrozenBalanceUsage`
- Entrypoint: reentrant contract exercising FreezeV2Util.queryFrozenBalanceUsage
- Attacker controls: request/transaction/contract inputs to `FreezeV2Util.queryFrozenBalanceUsage` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: reenters FreezeV2Util.queryFrozenBalanceUsage using a contract they control during a value/TRC10 transfer so balance is read before it is debited
- Invariant to test: FreezeV2Util.queryFrozenBalanceUsage debits before yielding control to callee
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test reentering on transfer asserting single debit
