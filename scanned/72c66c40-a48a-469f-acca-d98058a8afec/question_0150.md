# Q150: VMUtils: create2/address collision

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `VMUtils.compress` in `actuator/src/main/java/org/tron/core/vm/VMUtils.java` — where the attacker uses VMUtils.compress to deploy to a predicted address colliding with existing code/state — to break the invariant that address derivation in VMUtils.compress cannot overwrite existing contract state, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/VMUtils.java` -> `VMUtils.compress`
- Entrypoint: CREATE/CREATE2 via VMUtils.compress
- Attacker controls: request/transaction/contract inputs to `VMUtils.compress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses VMUtils.compress to deploy to a predicted address colliding with existing code/state
- Invariant to test: address derivation in VMUtils.compress cannot overwrite existing contract state
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test colliding deploy asserting no overwrite
