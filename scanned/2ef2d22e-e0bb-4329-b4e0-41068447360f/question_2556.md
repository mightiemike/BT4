# Q2556: Repeated pending-state queries can starve live processing via Public Grpc, Abci, Query / Attacker Can Repeat Request in Querier.AllExpiredBallotIDs

## Question
Can an unprivileged attacker enter through a public gRPC/ABCI query to the node with public gRPC, ABCI, or query inputs such as ids, pagination params, and repeated requests when the attacker can repeat the request pattern cheaply, and cause `Querier.AllExpiredBallotIDs` to trigger an unsafe state-transition edge case, so that it hammer collections the node must iterate heavily while live finalization also needs them, breaking the invariant that query-path iteration should not let public traffic materially block consensus-critical processing, and resulting in Inability to process and finalize new transactions?

## Target
- File/function: x/uvalidator/keeper/query_server.go::Querier.AllExpiredBallotIDs
- Entrypoint: a public gRPC/ABCI query to the node
- Attacker controls: public gRPC, ABCI, or query inputs such as ids, pagination params, and repeated requests
- Exploit idea: Cause `Querier.AllExpiredBallotIDs` to trigger an unsafe state-transition edge case, so it can hammer collections the node must iterate heavily while live finalization also needs them.
- Invariant to test: query-path iteration should not let public traffic materially block consensus-critical processing
- Expected Immunefi impact: Inability to process and finalize new transactions
- Fast validation: write a query-server test or benchmark that sends the crafted public request pattern and measure iteration, allocation, and error behavior
