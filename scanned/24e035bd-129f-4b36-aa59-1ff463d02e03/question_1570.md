# Q1570: History scans over active/finalized records become cheap attack loops via Keys Identifiers Force Lookups / Same Query Surface May in Keeper.AllExpiredInbounds

## Question
Can an unprivileged attacker enter through a public gRPC/ABCI query to the node with keys or identifiers that force lookups across large pending or historical collections when the same query surface may be hit while live finalization is ongoing, and cause `Keeper.AllExpiredInbounds` to trigger an unsafe state-transition edge case, so that it use repeated public queries over large historical collections to consume validator time cheaply, breaking the invariant that historical query surfaces must remain rate- and work-bounded enough for validator safety, and resulting in Widespread node overload or inability to finalize?

## Target
- File/function: x/uexecutor/keeper/query_server.go::Keeper.AllExpiredInbounds
- Entrypoint: a public gRPC/ABCI query to the node
- Attacker controls: keys or identifiers that force lookups across large pending or historical collections
- Exploit idea: Cause `Keeper.AllExpiredInbounds` to trigger an unsafe state-transition edge case, so it can use repeated public queries over large historical collections to consume validator time cheaply.
- Invariant to test: historical query surfaces must remain rate- and work-bounded enough for validator safety
- Expected Immunefi impact: Widespread node overload or inability to finalize
- Fast validation: write a query-server test or benchmark that sends the crafted public request pattern and measure iteration, allocation, and error behavior
