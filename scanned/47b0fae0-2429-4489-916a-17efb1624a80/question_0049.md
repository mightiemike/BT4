# Q49: Program: stack/depth bound bypass

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Program.setPreviouslyExecutedOp` in `actuator/src/main/java/org/tron/core/vm/program/Program.java` — where the attacker recurses or grows stack via Program.setPreviouslyExecutedOp past the depth/size bound without proportional cost — to break the invariant that Program.setPreviouslyExecutedOp enforces call depth and stack size before work, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Program.java` -> `Program.setPreviouslyExecutedOp`
- Entrypoint: deeply nested call reaching Program.setPreviouslyExecutedOp
- Attacker controls: request/transaction/contract inputs to `Program.setPreviouslyExecutedOp` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: recurses or grows stack via Program.setPreviouslyExecutedOp past the depth/size bound without proportional cost
- Invariant to test: Program.setPreviouslyExecutedOp enforces call depth and stack size before work
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test at depth boundary asserting revert not hang
