# Q653: VM: create2/address collision

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `VM.play` in `actuator/src/main/java/org/tron/core/vm/VM.java` — where the attacker uses VM.play to deploy to a predicted address colliding with existing code/state — to break the invariant that address derivation in VM.play cannot overwrite existing contract state, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/VM.java` -> `VM.play`
- Entrypoint: CREATE/CREATE2 via VM.play
- Attacker controls: request/transaction/contract inputs to `VM.play` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses VM.play to deploy to a predicted address colliding with existing code/state
- Invariant to test: address derivation in VM.play cannot overwrite existing contract state
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test colliding deploy asserting no overwrite
