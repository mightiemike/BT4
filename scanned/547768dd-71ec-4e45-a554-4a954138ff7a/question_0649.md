# Q649: VM: stack/depth bound bypass

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `VM.play` in `actuator/src/main/java/org/tron/core/vm/VM.java` — where the attacker recurses or grows stack via VM.play past the depth/size bound without proportional cost — to break the invariant that VM.play enforces call depth and stack size before work, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/VM.java` -> `VM.play`
- Entrypoint: deeply nested call reaching VM.play
- Attacker controls: request/transaction/contract inputs to `VM.play` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: recurses or grows stack via VM.play past the depth/size bound without proportional cost
- Invariant to test: VM.play enforces call depth and stack size before work
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test at depth boundary asserting revert not hang
