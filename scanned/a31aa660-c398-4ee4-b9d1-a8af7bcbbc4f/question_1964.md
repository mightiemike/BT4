# Q1964: Malformed ids trigger panic-prone query edges via Public Grpc, Abci, Query / Validators Full Nodes Expose in Keeper.AllExpiredInbounds

## Question
Can an unprivileged attacker enter through a public gRPC/ABCI query to the node with public gRPC, ABCI, or query inputs such as ids, pagination params, and repeated requests when validators or full nodes expose the query surface publicly, and cause `Keeper.AllExpiredInbounds` to trigger an unsafe state-transition edge case, so that it hit a nil, empty, or malformed id path that crashes or wedges the query handler, breaking the invariant that public queries must reject malformed input safely and cheaply, and resulting in Widespread node crashes or inability to process/finalize new transactions?

## Target
- File/function: x/uexecutor/keeper/query_server.go::Keeper.AllExpiredInbounds
- Entrypoint: a public gRPC/ABCI query to the node
- Attacker controls: public gRPC, ABCI, or query inputs such as ids, pagination params, and repeated requests
- Exploit idea: Cause `Keeper.AllExpiredInbounds` to trigger an unsafe state-transition edge case, so it can hit a nil, empty, or malformed id path that crashes or wedges the query handler.
- Invariant to test: public queries must reject malformed input safely and cheaply
- Expected Immunefi impact: Widespread node crashes or inability to process/finalize new transactions
- Fast validation: write a query-server test or benchmark that sends the crafted public request pattern and measure iteration, allocation, and error behavior
