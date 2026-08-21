# Q900: EnergyCost: create2/address collision

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `EnergyCost.getMidTierCost` in `actuator/src/main/java/org/tron/core/vm/EnergyCost.java` — where the attacker uses EnergyCost.getMidTierCost to deploy to a predicted address colliding with existing code/state — to break the invariant that address derivation in EnergyCost.getMidTierCost cannot overwrite existing contract state, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/EnergyCost.java` -> `EnergyCost.getMidTierCost`
- Entrypoint: CREATE/CREATE2 via EnergyCost.getMidTierCost
- Attacker controls: request/transaction/contract inputs to `EnergyCost.getMidTierCost` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses EnergyCost.getMidTierCost to deploy to a predicted address colliding with existing code/state
- Invariant to test: address derivation in EnergyCost.getMidTierCost cannot overwrite existing contract state
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test colliding deploy asserting no overwrite
