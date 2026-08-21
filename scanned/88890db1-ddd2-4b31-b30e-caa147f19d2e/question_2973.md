# Q2973: ContractEventParserAbi: node info disclosure

## Question
Can an unprivileged attacker (smart-contract/query) abuse `ContractEventParserAbi.parseEventData` in `framework/src/main/java/org/tron/common/logsfilter/ContractEventParserAbi.java` — where the attacker queries ContractEventParserAbi.parseEventData to read node internals that aid a further in-scope attack — to break the invariant that ContractEventParserAbi.parseEventData exposes no sensitive internal state to anonymous callers, leading to: Information disclosure (in-scope only if it enables impact)?

## Target
- File/function: `framework/src/main/java/org/tron/common/logsfilter/ContractEventParserAbi.java` -> `ContractEventParserAbi.parseEventData`
- Entrypoint: anonymous query to ContractEventParserAbi.parseEventData
- Attacker controls: request/transaction/contract inputs to `ContractEventParserAbi.parseEventData` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: queries ContractEventParserAbi.parseEventData to read node internals that aid a further in-scope attack
- Invariant to test: ContractEventParserAbi.parseEventData exposes no sensitive internal state to anonymous callers
- Expected Immunefi impact: Information disclosure (in-scope only if it enables impact)
- Fast validation: assert ContractEventParserAbi.parseEventData response omits sensitive fields
