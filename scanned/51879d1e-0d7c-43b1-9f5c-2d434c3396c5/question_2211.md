# Q2211: Storage: stack/depth bound bypass

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Storage.put` in `actuator/src/main/java/org/tron/core/vm/program/Storage.java` — where the attacker recurses or grows stack via Storage.put past the depth/size bound without proportional cost — to break the invariant that Storage.put enforces call depth and stack size before work, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Storage.java` -> `Storage.put`
- Entrypoint: deeply nested call reaching Storage.put
- Attacker controls: request/transaction/contract inputs to `Storage.put` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: recurses or grows stack via Storage.put past the depth/size bound without proportional cost
- Invariant to test: Storage.put enforces call depth and stack size before work
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test at depth boundary asserting revert not hang
