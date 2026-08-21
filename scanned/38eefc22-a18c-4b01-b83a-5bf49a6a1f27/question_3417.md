# Q3417: MessageCall: stack/depth bound bypass

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `MessageCall.getEnergy` in `actuator/src/main/java/org/tron/core/vm/MessageCall.java` — where the attacker recurses or grows stack via MessageCall.getEnergy past the depth/size bound without proportional cost — to break the invariant that MessageCall.getEnergy enforces call depth and stack size before work, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/MessageCall.java` -> `MessageCall.getEnergy`
- Entrypoint: deeply nested call reaching MessageCall.getEnergy
- Attacker controls: request/transaction/contract inputs to `MessageCall.getEnergy` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: recurses or grows stack via MessageCall.getEnergy past the depth/size bound without proportional cost
- Invariant to test: MessageCall.getEnergy enforces call depth and stack size before work
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test at depth boundary asserting revert not hang
