# Q1449: NodeInfoService: bloom/topic amplification

## Question
Can an unprivileged attacker (smart-contract/query) abuse `NodeInfoService.getNodeInfo` in `framework/src/main/java/org/tron/core/services/NodeInfoService.java` — where the attacker crafts topics so NodeInfoService.getNodeInfo bloom/section work grows disproportionately — to break the invariant that NodeInfoService.getNodeInfo work is bounded per event regardless of topic content, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/NodeInfoService.java` -> `NodeInfoService.getNodeInfo`
- Entrypoint: emit/query events via NodeInfoService.getNodeInfo
- Attacker controls: request/transaction/contract inputs to `NodeInfoService.getNodeInfo` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts topics so NodeInfoService.getNodeInfo bloom/section work grows disproportionately
- Invariant to test: NodeInfoService.getNodeInfo work is bounded per event regardless of topic content
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: measure NodeInfoService.getNodeInfo cost vs topic count
