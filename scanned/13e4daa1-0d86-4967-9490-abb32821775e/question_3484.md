# Q3484: FreezeV2Util: call value/token reentrancy

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `FreezeV2Util.queryResourceV2` in `actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java` — where the attacker reenters FreezeV2Util.queryResourceV2 using a contract they control during a value/TRC10 transfer so balance is read before it is debited — to break the invariant that FreezeV2Util.queryResourceV2 debits before yielding control to callee, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java` -> `FreezeV2Util.queryResourceV2`
- Entrypoint: reentrant contract exercising FreezeV2Util.queryResourceV2
- Attacker controls: request/transaction/contract inputs to `FreezeV2Util.queryResourceV2` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: reenters FreezeV2Util.queryResourceV2 using a contract they control during a value/TRC10 transfer so balance is read before it is debited
- Invariant to test: FreezeV2Util.queryResourceV2 debits before yielding control to callee
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test reentering on transfer asserting single debit
