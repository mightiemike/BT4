# Q1504: ContractEventParserAbi: bloom/topic amplification

## Question
Can an unprivileged attacker (smart-contract/query) abuse `ContractEventParserAbi.parseEventData` in `framework/src/main/java/org/tron/common/logsfilter/ContractEventParserAbi.java` — where the attacker crafts topics so ContractEventParserAbi.parseEventData bloom/section work grows disproportionately — to break the invariant that ContractEventParserAbi.parseEventData work is bounded per event regardless of topic content, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/common/logsfilter/ContractEventParserAbi.java` -> `ContractEventParserAbi.parseEventData`
- Entrypoint: emit/query events via ContractEventParserAbi.parseEventData
- Attacker controls: request/transaction/contract inputs to `ContractEventParserAbi.parseEventData` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts topics so ContractEventParserAbi.parseEventData bloom/section work grows disproportionately
- Invariant to test: ContractEventParserAbi.parseEventData work is bounded per event regardless of topic content
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: measure ContractEventParserAbi.parseEventData cost vs topic count
