# Q1282: MessageCall: stack/depth bound bypass

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `MessageCall.getInDataSize` in `actuator/src/main/java/org/tron/core/vm/MessageCall.java` — where the attacker recurses or grows stack via MessageCall.getInDataSize past the depth/size bound without proportional cost — to break the invariant that MessageCall.getInDataSize enforces call depth and stack size before work, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/MessageCall.java` -> `MessageCall.getInDataSize`
- Entrypoint: deeply nested call reaching MessageCall.getInDataSize
- Attacker controls: request/transaction/contract inputs to `MessageCall.getInDataSize` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: recurses or grows stack via MessageCall.getInDataSize past the depth/size bound without proportional cost
- Invariant to test: MessageCall.getInDataSize enforces call depth and stack size before work
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test at depth boundary asserting revert not hang
