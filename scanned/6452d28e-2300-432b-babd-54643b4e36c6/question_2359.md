# Q2359: Legacy compatibility synthesis becomes a public DoS vector via Malformed Nil Requests Sit / Validators Full Nodes Expose in Querier.AllExpiredBallotIDs

## Question
Can an unprivileged attacker enter through a public gRPC/ABCI query to the node with malformed or nil requests that sit on parser edges when validators or full nodes expose the query surface publicly, and cause `Querier.AllExpiredBallotIDs` to trigger an unsafe state-transition edge case, so that it route queries through expensive compatibility code that reconstructs large derived views, breaking the invariant that legacy query support must not let public callers trigger validator-expensive recomputation, and resulting in Widespread node overload or inability to finalize?

## Target
- File/function: x/uvalidator/keeper/query_server.go::Querier.AllExpiredBallotIDs
- Entrypoint: a public gRPC/ABCI query to the node
- Attacker controls: malformed or nil requests that sit on parser edges
- Exploit idea: Cause `Querier.AllExpiredBallotIDs` to trigger an unsafe state-transition edge case, so it can route queries through expensive compatibility code that reconstructs large derived views.
- Invariant to test: legacy query support must not let public callers trigger validator-expensive recomputation
- Expected Immunefi impact: Widespread node overload or inability to finalize
- Fast validation: write a query-server test or benchmark that sends the crafted public request pattern and measure iteration, allocation, and error behavior
