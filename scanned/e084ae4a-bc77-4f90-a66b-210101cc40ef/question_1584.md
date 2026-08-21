# Q1584: VMUtils: stack/depth bound bypass

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `VMUtils.compress` in `actuator/src/main/java/org/tron/core/vm/VMUtils.java` — where the attacker recurses or grows stack via VMUtils.compress past the depth/size bound without proportional cost — to break the invariant that VMUtils.compress enforces call depth and stack size before work, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/VMUtils.java` -> `VMUtils.compress`
- Entrypoint: deeply nested call reaching VMUtils.compress
- Attacker controls: request/transaction/contract inputs to `VMUtils.compress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: recurses or grows stack via VMUtils.compress past the depth/size bound without proportional cost
- Invariant to test: VMUtils.compress enforces call depth and stack size before work
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test at depth boundary asserting revert not hang
