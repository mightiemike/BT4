# Q2215: ContractTriggerCapsule: node info disclosure

## Question
Can an unprivileged attacker (smart-contract/query) abuse `ContractTriggerCapsule.processTrigger` in `framework/src/main/java/org/tron/common/logsfilter/capsule/ContractTriggerCapsule.java` — where the attacker queries ContractTriggerCapsule.processTrigger to read node internals that aid a further in-scope attack — to break the invariant that ContractTriggerCapsule.processTrigger exposes no sensitive internal state to anonymous callers, leading to: Information disclosure (in-scope only if it enables impact)?

## Target
- File/function: `framework/src/main/java/org/tron/common/logsfilter/capsule/ContractTriggerCapsule.java` -> `ContractTriggerCapsule.processTrigger`
- Entrypoint: anonymous query to ContractTriggerCapsule.processTrigger
- Attacker controls: request/transaction/contract inputs to `ContractTriggerCapsule.processTrigger` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: queries ContractTriggerCapsule.processTrigger to read node internals that aid a further in-scope attack
- Invariant to test: ContractTriggerCapsule.processTrigger exposes no sensitive internal state to anonymous callers
- Expected Immunefi impact: Information disclosure (in-scope only if it enables impact)
- Fast validation: assert ContractTriggerCapsule.processTrigger response omits sensitive fields
