# Q3148: History scans over active/finalized records become cheap attack loops via Public Grpc, Abci, Query / Queried Collection Can Grow in Querier.AllExpiredBallots

## Question
Can an unprivileged attacker enter through a public gRPC/ABCI query to the node with public gRPC, ABCI, or query inputs such as ids, pagination params, and repeated requests when the queried collection can grow under normal protocol use, and cause `Querier.AllExpiredBallots` to trigger an unsafe state-transition edge case, so that it use repeated public queries over large historical collections to consume validator time cheaply, breaking the invariant that historical query surfaces must remain rate- and work-bounded enough for validator safety, and resulting in Widespread node overload or inability to finalize?

## Target
- File/function: x/uvalidator/keeper/query_server.go::Querier.AllExpiredBallots
- Entrypoint: a public gRPC/ABCI query to the node
- Attacker controls: public gRPC, ABCI, or query inputs such as ids, pagination params, and repeated requests
- Exploit idea: Cause `Querier.AllExpiredBallots` to trigger an unsafe state-transition edge case, so it can use repeated public queries over large historical collections to consume validator time cheaply.
- Invariant to test: historical query surfaces must remain rate- and work-bounded enough for validator safety
- Expected Immunefi impact: Widespread node overload or inability to finalize
- Fast validation: write a query-server test or benchmark that sends the crafted public request pattern and measure iteration, allocation, and error behavior
