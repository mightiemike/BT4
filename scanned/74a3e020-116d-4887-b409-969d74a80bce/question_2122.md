# Q2122: Callback or rate-limit state confusion in HandleGatewayMessage

## Question
Can an unprivileged attacker abuse rate-limit keys, duplicate request IDs, and callback reuse timing at `public gateway JSON-RPC capability HTTP trigger or outbound web API capability request` so `HandleGatewayMessage` reuses callback state or charges rate limits to the wrong principal, leading to rate limit violations with real security impact and violating workflow resolution must map one request to exactly one authorized workflow owner?

## Target
- File/function: core/capabilities/webapi/outgoing_connector_handler.go::HandleGatewayMessage
- Entrypoint: public gateway JSON-RPC capability HTTP trigger or outbound web API capability request
- Attacker controls: rate-limit keys, duplicate request IDs, and callback reuse timing
- Exploit idea: Use conflicting workflow selectors, duplicated request IDs, and callback timing races to prove whether one authorized request can become another execution.
- Invariant to test: workflow resolution must map one request to exactly one authorized workflow owner
- Expected Immunefi impact: rate limit violations with real security impact
- Fast validation: Send duplicate request IDs and conflicting workflow selectors; assert exactly one authorized workflow and one callback slot are used.
