# Q10: RepositoryImpl: create2/address collision

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `RepositoryImpl.init` in `actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java` — where the attacker uses RepositoryImpl.init to deploy to a predicted address colliding with existing code/state — to break the invariant that address derivation in RepositoryImpl.init cannot overwrite existing contract state, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java` -> `RepositoryImpl.init`
- Entrypoint: CREATE/CREATE2 via RepositoryImpl.init
- Attacker controls: request/transaction/contract inputs to `RepositoryImpl.init` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses RepositoryImpl.init to deploy to a predicted address colliding with existing code/state
- Invariant to test: address derivation in RepositoryImpl.init cannot overwrite existing contract state
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test colliding deploy asserting no overwrite
