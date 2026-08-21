# Q799: OperationActions: stack/depth bound bypass

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `OperationActions.sdivAction` in `actuator/src/main/java/org/tron/core/vm/OperationActions.java` — where the attacker recurses or grows stack via OperationActions.sdivAction past the depth/size bound without proportional cost — to break the invariant that OperationActions.sdivAction enforces call depth and stack size before work, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/OperationActions.java` -> `OperationActions.sdivAction`
- Entrypoint: deeply nested call reaching OperationActions.sdivAction
- Attacker controls: request/transaction/contract inputs to `OperationActions.sdivAction` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: recurses or grows stack via OperationActions.sdivAction past the depth/size bound without proportional cost
- Invariant to test: OperationActions.sdivAction enforces call depth and stack size before work
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test at depth boundary asserting revert not hang
