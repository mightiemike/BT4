# Q411: NodeInfoService: attacker-controlled log parse

## Question
Can an unprivileged attacker (smart-contract/query) abuse `NodeInfoService.getNodeInfo` in `framework/src/main/java/org/tron/core/services/NodeInfoService.java` — where the attacker emits contract data that NodeInfoService.getNodeInfo parses into an oversized/malformed event, crashing or stalling the trigger pipeline — to break the invariant that NodeInfoService.getNodeInfo bounds and validates attacker-supplied event data, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/NodeInfoService.java` -> `NodeInfoService.getNodeInfo`
- Entrypoint: contract emitting data parsed by NodeInfoService.getNodeInfo
- Attacker controls: request/transaction/contract inputs to `NodeInfoService.getNodeInfo` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: emits contract data that NodeInfoService.getNodeInfo parses into an oversized/malformed event, crashing or stalling the trigger pipeline
- Invariant to test: NodeInfoService.getNodeInfo bounds and validates attacker-supplied event data
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit feeding malformed ABI data asserting bounded handling
