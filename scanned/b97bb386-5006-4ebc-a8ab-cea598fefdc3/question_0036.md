# Q36: TransactionLogTriggerCapsule: node info disclosure

## Question
Can an unprivileged attacker (smart-contract/query) abuse `TransactionLogTriggerCapsule.processTrigger` in `framework/src/main/java/org/tron/common/logsfilter/capsule/TransactionLogTriggerCapsule.java` — where the attacker queries TransactionLogTriggerCapsule.processTrigger to read node internals that aid a further in-scope attack — to break the invariant that TransactionLogTriggerCapsule.processTrigger exposes no sensitive internal state to anonymous callers, leading to: Information disclosure (in-scope only if it enables impact)?

## Target
- File/function: `framework/src/main/java/org/tron/common/logsfilter/capsule/TransactionLogTriggerCapsule.java` -> `TransactionLogTriggerCapsule.processTrigger`
- Entrypoint: anonymous query to TransactionLogTriggerCapsule.processTrigger
- Attacker controls: request/transaction/contract inputs to `TransactionLogTriggerCapsule.processTrigger` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: queries TransactionLogTriggerCapsule.processTrigger to read node internals that aid a further in-scope attack
- Invariant to test: TransactionLogTriggerCapsule.processTrigger exposes no sensitive internal state to anonymous callers
- Expected Immunefi impact: Information disclosure (in-scope only if it enables impact)
- Fast validation: assert TransactionLogTriggerCapsule.processTrigger response omits sensitive fields
