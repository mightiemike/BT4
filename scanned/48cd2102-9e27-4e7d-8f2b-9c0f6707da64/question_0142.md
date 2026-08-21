# Q142: ContractEventParser: node info disclosure

## Question
Can an unprivileged attacker (smart-contract/query) abuse `ContractEventParser.parseTopic` in `framework/src/main/java/org/tron/common/logsfilter/ContractEventParser.java` — where the attacker queries ContractEventParser.parseTopic to read node internals that aid a further in-scope attack — to break the invariant that ContractEventParser.parseTopic exposes no sensitive internal state to anonymous callers, leading to: Information disclosure (in-scope only if it enables impact)?

## Target
- File/function: `framework/src/main/java/org/tron/common/logsfilter/ContractEventParser.java` -> `ContractEventParser.parseTopic`
- Entrypoint: anonymous query to ContractEventParser.parseTopic
- Attacker controls: request/transaction/contract inputs to `ContractEventParser.parseTopic` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: queries ContractEventParser.parseTopic to read node internals that aid a further in-scope attack
- Invariant to test: ContractEventParser.parseTopic exposes no sensitive internal state to anonymous callers
- Expected Immunefi impact: Information disclosure (in-scope only if it enables impact)
- Fast validation: assert ContractEventParser.parseTopic response omits sensitive fields
