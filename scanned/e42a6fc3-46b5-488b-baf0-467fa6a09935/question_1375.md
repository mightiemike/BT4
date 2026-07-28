# Q1375: Cross-module query path allocates attacker-sized derived objects via Query Shapes Trigger Legacy / Queried Collection Can Grow in Querier.AllExpiredBallots

## Question
Can an unprivileged attacker enter through a public gRPC/ABCI query to the node with query shapes that trigger legacy compatibility synthesis or full-state walks when the queried collection can grow under normal protocol use, and cause `Querier.AllExpiredBallots` to trigger an unsafe state-transition edge case, so that it force the server to build large derived responses from user-controlled filters or ids, breaking the invariant that derived query responses must remain resource-bounded for public callers, and resulting in Widespread node overload or inability to finalize?

## Target
- File/function: x/uvalidator/keeper/query_server.go::Querier.AllExpiredBallots
- Entrypoint: a public gRPC/ABCI query to the node
- Attacker controls: query shapes that trigger legacy compatibility synthesis or full-state walks
- Exploit idea: Cause `Querier.AllExpiredBallots` to trigger an unsafe state-transition edge case, so it can force the server to build large derived responses from user-controlled filters or ids.
- Invariant to test: derived query responses must remain resource-bounded for public callers
- Expected Immunefi impact: Widespread node overload or inability to finalize
- Fast validation: write a query-server test or benchmark that sends the crafted public request pattern and measure iteration, allocation, and error behavior
