# Q2161: Pagination controls allow memory amplification via Keys Identifiers Force Lookups / Attacker Can Repeat Request in Keeper.AllExpiredInbounds

## Question
Can an unprivileged attacker enter through a public gRPC/ABCI query to the node with keys or identifiers that force lookups across large pending or historical collections when the attacker can repeat the request pattern cheaply, and cause `Keeper.AllExpiredInbounds` to trigger an unsafe state-transition edge case, so that it ask for pages or scans large enough to starve validator resources, breaking the invariant that query pagination must cap work and memory regardless of attacker input, and resulting in Widespread node overload or inability to finalize?

## Target
- File/function: x/uexecutor/keeper/query_server.go::Keeper.AllExpiredInbounds
- Entrypoint: a public gRPC/ABCI query to the node
- Attacker controls: keys or identifiers that force lookups across large pending or historical collections
- Exploit idea: Cause `Keeper.AllExpiredInbounds` to trigger an unsafe state-transition edge case, so it can ask for pages or scans large enough to starve validator resources.
- Invariant to test: query pagination must cap work and memory regardless of attacker input
- Expected Immunefi impact: Widespread node overload or inability to finalize
- Fast validation: write a query-server test or benchmark that sends the crafted public request pattern and measure iteration, allocation, and error behavior
