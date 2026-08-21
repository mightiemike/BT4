# Q2286: FreezeV2Util: non-deterministic result

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `FreezeV2Util.queryUnfreezableBalanceV2` in `actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java` — where the attacker finds an input to FreezeV2Util.queryUnfreezableBalanceV2 whose output depends on JDK/platform/iteration order, diverging node state — to break the invariant that FreezeV2Util.queryUnfreezableBalanceV2 is deterministic across JDK and node, leading to: Consensus divergence (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java` -> `FreezeV2Util.queryUnfreezableBalanceV2`
- Entrypoint: contract exercising FreezeV2Util.queryUnfreezableBalanceV2 edge input
- Attacker controls: request/transaction/contract inputs to `FreezeV2Util.queryUnfreezableBalanceV2` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to FreezeV2Util.queryUnfreezableBalanceV2 whose output depends on JDK/platform/iteration order, diverging node state
- Invariant to test: FreezeV2Util.queryUnfreezableBalanceV2 is deterministic across JDK and node
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential run across JDK8/17 asserting identical result
