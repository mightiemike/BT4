# Q3249: TransactionUtil: signature verification bypass

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransactionUtil.checkPermissionOperations` in `actuator/src/main/java/org/tron/core/utils/TransactionUtil.java` — where the attacker submits a transaction whose TransactionUtil.checkPermissionOperations accepts a missing, malleable, or duplicated signature as satisfying permission weight — to break the invariant that TransactionUtil.checkPermissionOperations requires signatures recovering to distinct keys summing to threshold weight, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/utils/TransactionUtil.java` -> `TransactionUtil.checkPermissionOperations`
- Entrypoint: broadcast a tx exercising TransactionUtil.checkPermissionOperations
- Attacker controls: request/transaction/contract inputs to `TransactionUtil.checkPermissionOperations` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a transaction whose TransactionUtil.checkPermissionOperations accepts a missing, malleable, or duplicated signature as satisfying permission weight
- Invariant to test: TransactionUtil.checkPermissionOperations requires signatures recovering to distinct keys summing to threshold weight
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit with empty/duplicate sig sets asserting rejection
