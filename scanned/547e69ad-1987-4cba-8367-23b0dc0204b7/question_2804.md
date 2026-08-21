# Q2804: Storage: create2/address collision

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Storage.put` in `actuator/src/main/java/org/tron/core/vm/program/Storage.java` — where the attacker uses Storage.put to deploy to a predicted address colliding with existing code/state — to break the invariant that address derivation in Storage.put cannot overwrite existing contract state, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Storage.java` -> `Storage.put`
- Entrypoint: CREATE/CREATE2 via Storage.put
- Attacker controls: request/transaction/contract inputs to `Storage.put` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses Storage.put to deploy to a predicted address colliding with existing code/state
- Invariant to test: address derivation in Storage.put cannot overwrite existing contract state
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test colliding deploy asserting no overwrite
