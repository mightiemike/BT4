# Q2096: ContractState: stack/depth bound bypass

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `ContractState.putNewContract` in `actuator/src/main/java/org/tron/core/vm/program/ContractState.java` — where the attacker recurses or grows stack via ContractState.putNewContract past the depth/size bound without proportional cost — to break the invariant that ContractState.putNewContract enforces call depth and stack size before work, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/ContractState.java` -> `ContractState.putNewContract`
- Entrypoint: deeply nested call reaching ContractState.putNewContract
- Attacker controls: request/transaction/contract inputs to `ContractState.putNewContract` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: recurses or grows stack via ContractState.putNewContract past the depth/size bound without proportional cost
- Invariant to test: ContractState.putNewContract enforces call depth and stack size before work
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test at depth boundary asserting revert not hang
