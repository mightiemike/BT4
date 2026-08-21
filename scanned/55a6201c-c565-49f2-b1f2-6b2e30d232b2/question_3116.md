# Q3116: TransactionUtil: expiration / ref-block replay

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransactionUtil.getTransactionId` in `actuator/src/main/java/org/tron/core/utils/TransactionUtil.java` — where the attacker replays a transaction past its intended window because TransactionUtil.getTransactionId mis-checks expiration or ref-block — to break the invariant that TransactionUtil.getTransactionId rejects expired or wrong-ref-block transactions, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/utils/TransactionUtil.java` -> `TransactionUtil.getTransactionId`
- Entrypoint: rebroadcast a tx through TransactionUtil.getTransactionId
- Attacker controls: request/transaction/contract inputs to `TransactionUtil.getTransactionId` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a transaction past its intended window because TransactionUtil.getTransactionId mis-checks expiration or ref-block
- Invariant to test: TransactionUtil.getTransactionId rejects expired or wrong-ref-block transactions
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit replaying at expiration boundary asserting rejection
