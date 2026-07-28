# Q3936: Legacy compatibility synthesis becomes a public DoS vector via Keys Identifiers Force Lookups / Attacker Can Repeat Request in Querier.AllExpiredBallots

## Question
Can an unprivileged attacker enter through a public gRPC/ABCI query to the node with keys or identifiers that force lookups across large pending or historical collections when the attacker can repeat the request pattern cheaply, and cause `Querier.AllExpiredBallots` to trigger an unsafe state-transition edge case, so that it route queries through expensive compatibility code that reconstructs large derived views, breaking the invariant that legacy query support must not let public callers trigger validator-expensive recomputation, and resulting in Widespread node overload or inability to finalize?

## Target
- File/function: x/uvalidator/keeper/query_server.go::Querier.AllExpiredBallots
- Entrypoint: a public gRPC/ABCI query to the node
- Attacker controls: keys or identifiers that force lookups across large pending or historical collections
- Exploit idea: Cause `Querier.AllExpiredBallots` to trigger an unsafe state-transition edge case, so it can route queries through expensive compatibility code that reconstructs large derived views.
- Invariant to test: legacy query support must not let public callers trigger validator-expensive recomputation
- Expected Immunefi impact: Widespread node overload or inability to finalize
- Fast validation: write a query-server test or benchmark that sends the crafted public request pattern and measure iteration, allocation, and error behavior
