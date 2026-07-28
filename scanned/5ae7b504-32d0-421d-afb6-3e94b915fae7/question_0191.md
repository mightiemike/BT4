# Q0191: Unauthenticated query can walk too much state via Query Shapes Trigger Legacy / Validators Full Nodes Expose in Keeper.AllExpiredInbounds

## Question
Can an unprivileged attacker enter through a public gRPC/ABCI query to the node with query shapes that trigger legacy compatibility synthesis or full-state walks when validators or full nodes expose the query surface publicly, and cause `Keeper.AllExpiredInbounds` to trigger an unsafe state-transition edge case, so that it use public query parameters to force an unbounded or unexpectedly large state walk, breaking the invariant that public query surfaces must remain bounded enough that one client cannot overload nodes materially, and resulting in Widespread node overload or inability to finalize?

## Target
- File/function: x/uexecutor/keeper/query_server.go::Keeper.AllExpiredInbounds
- Entrypoint: a public gRPC/ABCI query to the node
- Attacker controls: query shapes that trigger legacy compatibility synthesis or full-state walks
- Exploit idea: Cause `Keeper.AllExpiredInbounds` to trigger an unsafe state-transition edge case, so it can use public query parameters to force an unbounded or unexpectedly large state walk.
- Invariant to test: public query surfaces must remain bounded enough that one client cannot overload nodes materially
- Expected Immunefi impact: Widespread node overload or inability to finalize
- Fast validation: write a query-server test or benchmark that sends the crafted public request pattern and measure iteration, allocation, and error behavior
