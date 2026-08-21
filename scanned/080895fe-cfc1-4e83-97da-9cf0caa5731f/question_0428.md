# Q428: MessageCall: stack/depth bound bypass

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `MessageCall.getCodeAddress` in `actuator/src/main/java/org/tron/core/vm/MessageCall.java` — where the attacker recurses or grows stack via MessageCall.getCodeAddress past the depth/size bound without proportional cost — to break the invariant that MessageCall.getCodeAddress enforces call depth and stack size before work, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/MessageCall.java` -> `MessageCall.getCodeAddress`
- Entrypoint: deeply nested call reaching MessageCall.getCodeAddress
- Attacker controls: request/transaction/contract inputs to `MessageCall.getCodeAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: recurses or grows stack via MessageCall.getCodeAddress past the depth/size bound without proportional cost
- Invariant to test: MessageCall.getCodeAddress enforces call depth and stack size before work
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test at depth boundary asserting revert not hang
