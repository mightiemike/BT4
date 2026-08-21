# Q2128: FreezeV2Util: stack/depth bound bypass

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `FreezeV2Util.queryDelegatableResource` in `actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java` — where the attacker recurses or grows stack via FreezeV2Util.queryDelegatableResource past the depth/size bound without proportional cost — to break the invariant that FreezeV2Util.queryDelegatableResource enforces call depth and stack size before work, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java` -> `FreezeV2Util.queryDelegatableResource`
- Entrypoint: deeply nested call reaching FreezeV2Util.queryDelegatableResource
- Attacker controls: request/transaction/contract inputs to `FreezeV2Util.queryDelegatableResource` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: recurses or grows stack via FreezeV2Util.queryDelegatableResource past the depth/size bound without proportional cost
- Invariant to test: FreezeV2Util.queryDelegatableResource enforces call depth and stack size before work
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test at depth boundary asserting revert not hang
