# Q2949: Cross-module query path allocates attacker-sized derived objects via Keys Identifiers Force Lookups / Attacker Can Repeat Request in Keeper.AllExpiredInbounds

## Question
Can an unprivileged attacker enter through a public gRPC/ABCI query to the node with keys or identifiers that force lookups across large pending or historical collections when the attacker can repeat the request pattern cheaply, and cause `Keeper.AllExpiredInbounds` to trigger an unsafe state-transition edge case, so that it force the server to build large derived responses from user-controlled filters or ids, breaking the invariant that derived query responses must remain resource-bounded for public callers, and resulting in Widespread node overload or inability to finalize?

## Target
- File/function: x/uexecutor/keeper/query_server.go::Keeper.AllExpiredInbounds
- Entrypoint: a public gRPC/ABCI query to the node
- Attacker controls: keys or identifiers that force lookups across large pending or historical collections
- Exploit idea: Cause `Keeper.AllExpiredInbounds` to trigger an unsafe state-transition edge case, so it can force the server to build large derived responses from user-controlled filters or ids.
- Invariant to test: derived query responses must remain resource-bounded for public callers
- Expected Immunefi impact: Widespread node overload or inability to finalize
- Fast validation: write a query-server test or benchmark that sends the crafted public request pattern and measure iteration, allocation, and error behavior
