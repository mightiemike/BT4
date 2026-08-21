# Q1758: VMUtils: create2/address collision

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `VMUtils.writeStringToFile` in `actuator/src/main/java/org/tron/core/vm/VMUtils.java` — where the attacker uses VMUtils.writeStringToFile to deploy to a predicted address colliding with existing code/state — to break the invariant that address derivation in VMUtils.writeStringToFile cannot overwrite existing contract state, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/VMUtils.java` -> `VMUtils.writeStringToFile`
- Entrypoint: CREATE/CREATE2 via VMUtils.writeStringToFile
- Attacker controls: request/transaction/contract inputs to `VMUtils.writeStringToFile` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses VMUtils.writeStringToFile to deploy to a predicted address colliding with existing code/state
- Invariant to test: address derivation in VMUtils.writeStringToFile cannot overwrite existing contract state
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test colliding deploy asserting no overwrite
