# Q3121: ContractEventParser: bloom/topic amplification

## Question
Can an unprivileged attacker (smart-contract/query) abuse `ContractEventParser.parseDataBytes` in `framework/src/main/java/org/tron/common/logsfilter/ContractEventParser.java` — where the attacker crafts topics so ContractEventParser.parseDataBytes bloom/section work grows disproportionately — to break the invariant that ContractEventParser.parseDataBytes work is bounded per event regardless of topic content, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/common/logsfilter/ContractEventParser.java` -> `ContractEventParser.parseDataBytes`
- Entrypoint: emit/query events via ContractEventParser.parseDataBytes
- Attacker controls: request/transaction/contract inputs to `ContractEventParser.parseDataBytes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts topics so ContractEventParser.parseDataBytes bloom/section work grows disproportionately
- Invariant to test: ContractEventParser.parseDataBytes work is bounded per event regardless of topic content
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: measure ContractEventParser.parseDataBytes cost vs topic count
