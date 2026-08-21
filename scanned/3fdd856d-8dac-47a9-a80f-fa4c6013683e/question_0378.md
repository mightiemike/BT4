# Q378: Memory: create2/address collision

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Memory.readWord` in `actuator/src/main/java/org/tron/core/vm/program/Memory.java` — where the attacker uses Memory.readWord to deploy to a predicted address colliding with existing code/state — to break the invariant that address derivation in Memory.readWord cannot overwrite existing contract state, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Memory.java` -> `Memory.readWord`
- Entrypoint: CREATE/CREATE2 via Memory.readWord
- Attacker controls: request/transaction/contract inputs to `Memory.readWord` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses Memory.readWord to deploy to a predicted address colliding with existing code/state
- Invariant to test: address derivation in Memory.readWord cannot overwrite existing contract state
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test colliding deploy asserting no overwrite
