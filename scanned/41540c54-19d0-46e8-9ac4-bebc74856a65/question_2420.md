# Q2420: ConfigLoader: stack/depth bound bypass

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `ConfigLoader.load` in `actuator/src/main/java/org/tron/core/vm/config/ConfigLoader.java` — where the attacker recurses or grows stack via ConfigLoader.load past the depth/size bound without proportional cost — to break the invariant that ConfigLoader.load enforces call depth and stack size before work, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/config/ConfigLoader.java` -> `ConfigLoader.load`
- Entrypoint: deeply nested call reaching ConfigLoader.load
- Attacker controls: request/transaction/contract inputs to `ConfigLoader.load` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: recurses or grows stack via ConfigLoader.load past the depth/size bound without proportional cost
- Invariant to test: ConfigLoader.load enforces call depth and stack size before work
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test at depth boundary asserting revert not hang
