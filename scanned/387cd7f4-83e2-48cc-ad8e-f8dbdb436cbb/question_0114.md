# Q114: TransactionLogTriggerCapsule: bloom/topic amplification

## Question
Can an unprivileged attacker (smart-contract/query) abuse `TransactionLogTriggerCapsule.processTrigger` in `framework/src/main/java/org/tron/common/logsfilter/capsule/TransactionLogTriggerCapsule.java` — where the attacker crafts topics so TransactionLogTriggerCapsule.processTrigger bloom/section work grows disproportionately — to break the invariant that TransactionLogTriggerCapsule.processTrigger work is bounded per event regardless of topic content, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/common/logsfilter/capsule/TransactionLogTriggerCapsule.java` -> `TransactionLogTriggerCapsule.processTrigger`
- Entrypoint: emit/query events via TransactionLogTriggerCapsule.processTrigger
- Attacker controls: request/transaction/contract inputs to `TransactionLogTriggerCapsule.processTrigger` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts topics so TransactionLogTriggerCapsule.processTrigger bloom/section work grows disproportionately
- Invariant to test: TransactionLogTriggerCapsule.processTrigger work is bounded per event regardless of topic content
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: measure TransactionLogTriggerCapsule.processTrigger cost vs topic count
