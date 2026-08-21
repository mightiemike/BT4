# Q3662: MessageCall: create2/address collision

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `MessageCall.getOpCode` in `actuator/src/main/java/org/tron/core/vm/MessageCall.java` — where the attacker uses MessageCall.getOpCode to deploy to a predicted address colliding with existing code/state — to break the invariant that address derivation in MessageCall.getOpCode cannot overwrite existing contract state, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/MessageCall.java` -> `MessageCall.getOpCode`
- Entrypoint: CREATE/CREATE2 via MessageCall.getOpCode
- Attacker controls: request/transaction/contract inputs to `MessageCall.getOpCode` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses MessageCall.getOpCode to deploy to a predicted address colliding with existing code/state
- Invariant to test: address derivation in MessageCall.getOpCode cannot overwrite existing contract state
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test colliding deploy asserting no overwrite
