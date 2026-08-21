# Q2713: RepositoryImpl: stack/depth bound bypass

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `RepositoryImpl.createRoot` in `actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java` — where the attacker recurses or grows stack via RepositoryImpl.createRoot past the depth/size bound without proportional cost — to break the invariant that RepositoryImpl.createRoot enforces call depth and stack size before work, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java` -> `RepositoryImpl.createRoot`
- Entrypoint: deeply nested call reaching RepositoryImpl.createRoot
- Attacker controls: request/transaction/contract inputs to `RepositoryImpl.createRoot` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: recurses or grows stack via RepositoryImpl.createRoot past the depth/size bound without proportional cost
- Invariant to test: RepositoryImpl.createRoot enforces call depth and stack size before work
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test at depth boundary asserting revert not hang
