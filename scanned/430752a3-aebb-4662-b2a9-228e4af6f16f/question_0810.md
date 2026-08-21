# Q810: MessageCall: create2/address collision

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `MessageCall.getInDataOffs` in `actuator/src/main/java/org/tron/core/vm/MessageCall.java` — where the attacker uses MessageCall.getInDataOffs to deploy to a predicted address colliding with existing code/state — to break the invariant that address derivation in MessageCall.getInDataOffs cannot overwrite existing contract state, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/MessageCall.java` -> `MessageCall.getInDataOffs`
- Entrypoint: CREATE/CREATE2 via MessageCall.getInDataOffs
- Attacker controls: request/transaction/contract inputs to `MessageCall.getInDataOffs` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses MessageCall.getInDataOffs to deploy to a predicted address colliding with existing code/state
- Invariant to test: address derivation in MessageCall.getInDataOffs cannot overwrite existing contract state
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test colliding deploy asserting no overwrite
