# Q3344: Unauthenticated query can walk too much state via Keys Identifiers Force Lookups / Validators Full Nodes Expose in Querier.AllExpiredBallotIDs

## Question
Can an unprivileged attacker enter through a public gRPC/ABCI query to the node with keys or identifiers that force lookups across large pending or historical collections when validators or full nodes expose the query surface publicly, and cause `Querier.AllExpiredBallotIDs` to trigger an unsafe state-transition edge case, so that it use public query parameters to force an unbounded or unexpectedly large state walk, breaking the invariant that public query surfaces must remain bounded enough that one client cannot overload nodes materially, and resulting in Widespread node overload or inability to finalize?

## Target
- File/function: x/uvalidator/keeper/query_server.go::Querier.AllExpiredBallotIDs
- Entrypoint: a public gRPC/ABCI query to the node
- Attacker controls: keys or identifiers that force lookups across large pending or historical collections
- Exploit idea: Cause `Querier.AllExpiredBallotIDs` to trigger an unsafe state-transition edge case, so it can use public query parameters to force an unbounded or unexpectedly large state walk.
- Invariant to test: public query surfaces must remain bounded enough that one client cannot overload nodes materially
- Expected Immunefi impact: Widespread node overload or inability to finalize
- Fast validation: write a query-server test or benchmark that sends the crafted public request pattern and measure iteration, allocation, and error behavior
