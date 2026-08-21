# Q1384: Stack: create2/address collision

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Stack.pop` in `actuator/src/main/java/org/tron/core/vm/program/Stack.java` — where the attacker uses Stack.pop to deploy to a predicted address colliding with existing code/state — to break the invariant that address derivation in Stack.pop cannot overwrite existing contract state, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Stack.java` -> `Stack.pop`
- Entrypoint: CREATE/CREATE2 via Stack.pop
- Attacker controls: request/transaction/contract inputs to `Stack.pop` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses Stack.pop to deploy to a predicted address colliding with existing code/state
- Invariant to test: address derivation in Stack.pop cannot overwrite existing contract state
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test colliding deploy asserting no overwrite
