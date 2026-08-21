# Q2791: ContractState: stack/depth bound bypass

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `ContractState.updateContract` in `actuator/src/main/java/org/tron/core/vm/program/ContractState.java` — where the attacker recurses or grows stack via ContractState.updateContract past the depth/size bound without proportional cost — to break the invariant that ContractState.updateContract enforces call depth and stack size before work, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/ContractState.java` -> `ContractState.updateContract`
- Entrypoint: deeply nested call reaching ContractState.updateContract
- Attacker controls: request/transaction/contract inputs to `ContractState.updateContract` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: recurses or grows stack via ContractState.updateContract past the depth/size bound without proportional cost
- Invariant to test: ContractState.updateContract enforces call depth and stack size before work
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test at depth boundary asserting revert not hang
