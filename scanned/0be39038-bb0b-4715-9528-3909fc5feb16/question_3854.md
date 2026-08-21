# Q3854: ContractEventParserAbi: node info disclosure

## Question
Can an unprivileged attacker (smart-contract/query) abuse `ContractEventParserAbi.parseTopics` in `framework/src/main/java/org/tron/common/logsfilter/ContractEventParserAbi.java` — where the attacker queries ContractEventParserAbi.parseTopics to read node internals that aid a further in-scope attack — to break the invariant that ContractEventParserAbi.parseTopics exposes no sensitive internal state to anonymous callers, leading to: Information disclosure (in-scope only if it enables impact)?

## Target
- File/function: `framework/src/main/java/org/tron/common/logsfilter/ContractEventParserAbi.java` -> `ContractEventParserAbi.parseTopics`
- Entrypoint: anonymous query to ContractEventParserAbi.parseTopics
- Attacker controls: request/transaction/contract inputs to `ContractEventParserAbi.parseTopics` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: queries ContractEventParserAbi.parseTopics to read node internals that aid a further in-scope attack
- Invariant to test: ContractEventParserAbi.parseTopics exposes no sensitive internal state to anonymous callers
- Expected Immunefi impact: Information disclosure (in-scope only if it enables impact)
- Fast validation: assert ContractEventParserAbi.parseTopics response omits sensitive fields
