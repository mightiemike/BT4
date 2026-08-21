# Q2392: OperationActions: stack/depth bound bypass

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `OperationActions.subAction` in `actuator/src/main/java/org/tron/core/vm/OperationActions.java` — where the attacker recurses or grows stack via OperationActions.subAction past the depth/size bound without proportional cost — to break the invariant that OperationActions.subAction enforces call depth and stack size before work, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/OperationActions.java` -> `OperationActions.subAction`
- Entrypoint: deeply nested call reaching OperationActions.subAction
- Attacker controls: request/transaction/contract inputs to `OperationActions.subAction` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: recurses or grows stack via OperationActions.subAction past the depth/size bound without proportional cost
- Invariant to test: OperationActions.subAction enforces call depth and stack size before work
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test at depth boundary asserting revert not hang
