# Q19: Program: stack/depth bound bypass

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Program.getPreviouslyExecutedOp` in `actuator/src/main/java/org/tron/core/vm/program/Program.java` — where the attacker recurses or grows stack via Program.getPreviouslyExecutedOp past the depth/size bound without proportional cost — to break the invariant that Program.getPreviouslyExecutedOp enforces call depth and stack size before work, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Program.java` -> `Program.getPreviouslyExecutedOp`
- Entrypoint: deeply nested call reaching Program.getPreviouslyExecutedOp
- Attacker controls: request/transaction/contract inputs to `Program.getPreviouslyExecutedOp` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: recurses or grows stack via Program.getPreviouslyExecutedOp past the depth/size bound without proportional cost
- Invariant to test: Program.getPreviouslyExecutedOp enforces call depth and stack size before work
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test at depth boundary asserting revert not hang
