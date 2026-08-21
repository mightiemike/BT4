# Q3938: Stack: stack/depth bound bypass

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Stack.swap` in `actuator/src/main/java/org/tron/core/vm/program/Stack.java` — where the attacker recurses or grows stack via Stack.swap past the depth/size bound without proportional cost — to break the invariant that Stack.swap enforces call depth and stack size before work, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Stack.java` -> `Stack.swap`
- Entrypoint: deeply nested call reaching Stack.swap
- Attacker controls: request/transaction/contract inputs to `Stack.swap` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: recurses or grows stack via Stack.swap past the depth/size bound without proportional cost
- Invariant to test: Stack.swap enforces call depth and stack size before work
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test at depth boundary asserting revert not hang
