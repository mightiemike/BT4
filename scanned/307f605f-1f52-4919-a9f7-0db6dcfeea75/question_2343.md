# Q2343: VMUtils: create2/address collision

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `VMUtils.write` in `actuator/src/main/java/org/tron/core/vm/VMUtils.java` — where the attacker uses VMUtils.write to deploy to a predicted address colliding with existing code/state — to break the invariant that address derivation in VMUtils.write cannot overwrite existing contract state, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/VMUtils.java` -> `VMUtils.write`
- Entrypoint: CREATE/CREATE2 via VMUtils.write
- Attacker controls: request/transaction/contract inputs to `VMUtils.write` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses VMUtils.write to deploy to a predicted address colliding with existing code/state
- Invariant to test: address derivation in VMUtils.write cannot overwrite existing contract state
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test colliding deploy asserting no overwrite
