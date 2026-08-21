# Q3123: TransactionLogTriggerCapsule: bloom/topic amplification

## Question
Can an unprivileged attacker (smart-contract/query) abuse `TransactionLogTriggerCapsule.getInternalTransactionList` in `framework/src/main/java/org/tron/common/logsfilter/capsule/TransactionLogTriggerCapsule.java` — where the attacker crafts topics so TransactionLogTriggerCapsule.getInternalTransactionList bloom/section work grows disproportionately — to break the invariant that TransactionLogTriggerCapsule.getInternalTransactionList work is bounded per event regardless of topic content, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/common/logsfilter/capsule/TransactionLogTriggerCapsule.java` -> `TransactionLogTriggerCapsule.getInternalTransactionList`
- Entrypoint: emit/query events via TransactionLogTriggerCapsule.getInternalTransactionList
- Attacker controls: request/transaction/contract inputs to `TransactionLogTriggerCapsule.getInternalTransactionList` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts topics so TransactionLogTriggerCapsule.getInternalTransactionList bloom/section work grows disproportionately
- Invariant to test: TransactionLogTriggerCapsule.getInternalTransactionList work is bounded per event regardless of topic content
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: measure TransactionLogTriggerCapsule.getInternalTransactionList cost vs topic count
