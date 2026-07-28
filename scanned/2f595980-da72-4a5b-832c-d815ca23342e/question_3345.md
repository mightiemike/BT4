# Q3345: Unauthenticated query can walk too much state via Query Shapes Trigger Legacy / Queried Collection Can Grow in Querier.AllExpiredBallots

## Question
Can an unprivileged attacker enter through a public gRPC/ABCI query to the node with query shapes that trigger legacy compatibility synthesis or full-state walks when the queried collection can grow under normal protocol use, and cause `Querier.AllExpiredBallots` to trigger an unsafe state-transition edge case, so that it use public query parameters to force an unbounded or unexpectedly large state walk, breaking the invariant that public query surfaces must remain bounded enough that one client cannot overload nodes materially, and resulting in Widespread node overload or inability to finalize?

## Target
- File/function: x/uvalidator/keeper/query_server.go::Querier.AllExpiredBallots
- Entrypoint: a public gRPC/ABCI query to the node
- Attacker controls: query shapes that trigger legacy compatibility synthesis or full-state walks
- Exploit idea: Cause `Querier.AllExpiredBallots` to trigger an unsafe state-transition edge case, so it can use public query parameters to force an unbounded or unexpectedly large state walk.
- Invariant to test: public query surfaces must remain bounded enough that one client cannot overload nodes materially
- Expected Immunefi impact: Widespread node overload or inability to finalize
- Fast validation: write a query-server test or benchmark that sends the crafted public request pattern and measure iteration, allocation, and error behavior
