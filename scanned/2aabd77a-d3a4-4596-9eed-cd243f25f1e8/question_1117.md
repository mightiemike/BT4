# Q1117: AccountCapsule: signature verification bypass

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AccountCapsule.setDefaultWitnessPermission` in `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` — where the attacker submits a transaction whose AccountCapsule.setDefaultWitnessPermission accepts a missing, malleable, or duplicated signature as satisfying permission weight — to break the invariant that AccountCapsule.setDefaultWitnessPermission requires signatures recovering to distinct keys summing to threshold weight, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` -> `AccountCapsule.setDefaultWitnessPermission`
- Entrypoint: broadcast a tx exercising AccountCapsule.setDefaultWitnessPermission
- Attacker controls: request/transaction/contract inputs to `AccountCapsule.setDefaultWitnessPermission` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a transaction whose AccountCapsule.setDefaultWitnessPermission accepts a missing, malleable, or duplicated signature as satisfying permission weight
- Invariant to test: AccountCapsule.setDefaultWitnessPermission requires signatures recovering to distinct keys summing to threshold weight
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit with empty/duplicate sig sets asserting rejection
