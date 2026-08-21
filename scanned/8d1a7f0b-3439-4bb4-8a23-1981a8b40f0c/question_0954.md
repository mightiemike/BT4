# Q954: OperationRegistry: stack/depth bound bypass

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `OperationRegistry.newTronV12OperationSet` in `actuator/src/main/java/org/tron/core/vm/OperationRegistry.java` — where the attacker recurses or grows stack via OperationRegistry.newTronV12OperationSet past the depth/size bound without proportional cost — to break the invariant that OperationRegistry.newTronV12OperationSet enforces call depth and stack size before work, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/OperationRegistry.java` -> `OperationRegistry.newTronV12OperationSet`
- Entrypoint: deeply nested call reaching OperationRegistry.newTronV12OperationSet
- Attacker controls: request/transaction/contract inputs to `OperationRegistry.newTronV12OperationSet` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: recurses or grows stack via OperationRegistry.newTronV12OperationSet past the depth/size bound without proportional cost
- Invariant to test: OperationRegistry.newTronV12OperationSet enforces call depth and stack size before work
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test at depth boundary asserting revert not hang
