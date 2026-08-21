# Q2600: MUtil: stack/depth bound bypass

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `MUtil.checkCPUTimeForModExp` in `actuator/src/main/java/org/tron/core/vm/utils/MUtil.java` — where the attacker recurses or grows stack via MUtil.checkCPUTimeForModExp past the depth/size bound without proportional cost — to break the invariant that MUtil.checkCPUTimeForModExp enforces call depth and stack size before work, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/utils/MUtil.java` -> `MUtil.checkCPUTimeForModExp`
- Entrypoint: deeply nested call reaching MUtil.checkCPUTimeForModExp
- Attacker controls: request/transaction/contract inputs to `MUtil.checkCPUTimeForModExp` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: recurses or grows stack via MUtil.checkCPUTimeForModExp past the depth/size bound without proportional cost
- Invariant to test: MUtil.checkCPUTimeForModExp enforces call depth and stack size before work
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test at depth boundary asserting revert not hang
