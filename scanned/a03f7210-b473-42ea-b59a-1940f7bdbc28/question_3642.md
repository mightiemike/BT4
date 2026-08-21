# Q3642: TransactionUtil: signature verification bypass

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransactionUtil.truncateSignatures` in `actuator/src/main/java/org/tron/core/utils/TransactionUtil.java` — where the attacker submits a transaction whose TransactionUtil.truncateSignatures accepts a missing, malleable, or duplicated signature as satisfying permission weight — to break the invariant that TransactionUtil.truncateSignatures requires signatures recovering to distinct keys summing to threshold weight, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/utils/TransactionUtil.java` -> `TransactionUtil.truncateSignatures`
- Entrypoint: broadcast a tx exercising TransactionUtil.truncateSignatures
- Attacker controls: request/transaction/contract inputs to `TransactionUtil.truncateSignatures` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a transaction whose TransactionUtil.truncateSignatures accepts a missing, malleable, or duplicated signature as satisfying permission weight
- Invariant to test: TransactionUtil.truncateSignatures requires signatures recovering to distinct keys summing to threshold weight
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit with empty/duplicate sig sets asserting rejection
