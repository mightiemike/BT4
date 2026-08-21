# Q676: RepositoryImpl: stack/depth bound bypass

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `RepositoryImpl.init` in `actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java` — where the attacker recurses or grows stack via RepositoryImpl.init past the depth/size bound without proportional cost — to break the invariant that RepositoryImpl.init enforces call depth and stack size before work, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java` -> `RepositoryImpl.init`
- Entrypoint: deeply nested call reaching RepositoryImpl.init
- Attacker controls: request/transaction/contract inputs to `RepositoryImpl.init` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: recurses or grows stack via RepositoryImpl.init past the depth/size bound without proportional cost
- Invariant to test: RepositoryImpl.init enforces call depth and stack size before work
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test at depth boundary asserting revert not hang
