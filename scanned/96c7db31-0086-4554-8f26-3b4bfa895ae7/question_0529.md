# Q529: Manager: signature verification bypass

## Question
Can an unprivileged attacker (broadcast transaction) abuse `Manager.resetBlackholeAccountPermission` in `framework/src/main/java/org/tron/core/db/Manager.java` — where the attacker submits a transaction whose Manager.resetBlackholeAccountPermission accepts a missing, malleable, or duplicated signature as satisfying permission weight — to break the invariant that Manager.resetBlackholeAccountPermission requires signatures recovering to distinct keys summing to threshold weight, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/db/Manager.java` -> `Manager.resetBlackholeAccountPermission`
- Entrypoint: broadcast a tx exercising Manager.resetBlackholeAccountPermission
- Attacker controls: request/transaction/contract inputs to `Manager.resetBlackholeAccountPermission` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a transaction whose Manager.resetBlackholeAccountPermission accepts a missing, malleable, or duplicated signature as satisfying permission weight
- Invariant to test: Manager.resetBlackholeAccountPermission requires signatures recovering to distinct keys summing to threshold weight
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit with empty/duplicate sig sets asserting rejection
