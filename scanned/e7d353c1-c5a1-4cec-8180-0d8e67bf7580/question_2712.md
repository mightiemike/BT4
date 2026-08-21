# Q2712: ContractTriggerCapsule: bloom/topic amplification

## Question
Can an unprivileged attacker (smart-contract/query) abuse `ContractTriggerCapsule.processTrigger` in `framework/src/main/java/org/tron/common/logsfilter/capsule/ContractTriggerCapsule.java` — where the attacker crafts topics so ContractTriggerCapsule.processTrigger bloom/section work grows disproportionately — to break the invariant that ContractTriggerCapsule.processTrigger work is bounded per event regardless of topic content, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/common/logsfilter/capsule/ContractTriggerCapsule.java` -> `ContractTriggerCapsule.processTrigger`
- Entrypoint: emit/query events via ContractTriggerCapsule.processTrigger
- Attacker controls: request/transaction/contract inputs to `ContractTriggerCapsule.processTrigger` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts topics so ContractTriggerCapsule.processTrigger bloom/section work grows disproportionately
- Invariant to test: ContractTriggerCapsule.processTrigger work is bounded per event regardless of topic content
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: measure ContractTriggerCapsule.processTrigger cost vs topic count
