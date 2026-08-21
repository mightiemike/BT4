# Q1457: ContractEventParser: bloom/topic amplification

## Question
Can an unprivileged attacker (smart-contract/query) abuse `ContractEventParser.parseTopic` in `framework/src/main/java/org/tron/common/logsfilter/ContractEventParser.java` — where the attacker crafts topics so ContractEventParser.parseTopic bloom/section work grows disproportionately — to break the invariant that ContractEventParser.parseTopic work is bounded per event regardless of topic content, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/common/logsfilter/ContractEventParser.java` -> `ContractEventParser.parseTopic`
- Entrypoint: emit/query events via ContractEventParser.parseTopic
- Attacker controls: request/transaction/contract inputs to `ContractEventParser.parseTopic` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts topics so ContractEventParser.parseTopic bloom/section work grows disproportionately
- Invariant to test: ContractEventParser.parseTopic work is bounded per event regardless of topic content
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: measure ContractEventParser.parseTopic cost vs topic count
