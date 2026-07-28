# Q0979: Repeated pending-state queries can starve live processing via Query Shapes Trigger Legacy / Validators Full Nodes Expose in Keeper.AllExpiredInbounds

## Question
Can an unprivileged attacker enter through a public gRPC/ABCI query to the node with query shapes that trigger legacy compatibility synthesis or full-state walks when validators or full nodes expose the query surface publicly, and cause `Keeper.AllExpiredInbounds` to trigger an unsafe state-transition edge case, so that it hammer collections the node must iterate heavily while live finalization also needs them, breaking the invariant that query-path iteration should not let public traffic materially block consensus-critical processing, and resulting in Inability to process and finalize new transactions?

## Target
- File/function: x/uexecutor/keeper/query_server.go::Keeper.AllExpiredInbounds
- Entrypoint: a public gRPC/ABCI query to the node
- Attacker controls: query shapes that trigger legacy compatibility synthesis or full-state walks
- Exploit idea: Cause `Keeper.AllExpiredInbounds` to trigger an unsafe state-transition edge case, so it can hammer collections the node must iterate heavily while live finalization also needs them.
- Invariant to test: query-path iteration should not let public traffic materially block consensus-critical processing
- Expected Immunefi impact: Inability to process and finalize new transactions
- Fast validation: write a query-server test or benchmark that sends the crafted public request pattern and measure iteration, allocation, and error behavior
