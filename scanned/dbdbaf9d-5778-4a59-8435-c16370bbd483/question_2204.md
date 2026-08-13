# Q2204: Request-ID replay or collision in HandleGatewayMessage

## Question
Can an unprivileged attacker exploit workflowID, workflowOwner, workflowName, and workflowTag fields at `public gateway JSON-RPC capability HTTP trigger or outbound web API capability request` so `HandleGatewayMessage` accepts duplicate, normalized, or cross-format request IDs that bypass replay/correlation checks, leading to retrieve protected data or secrets through cross-workflow confusion and violating request IDs and callbacks must not replay, collide, or cross-bind between users or workflows?

## Target
- File/function: core/capabilities/webapi/trigger/trigger.go::HandleGatewayMessage
- Entrypoint: public gateway JSON-RPC capability HTTP trigger or outbound web API capability request
- Attacker controls: workflowID, workflowOwner, workflowName, and workflowTag fields
- Exploit idea: Use conflicting workflow selectors, duplicated request IDs, and callback timing races to prove whether one authorized request can become another execution.
- Invariant to test: request IDs and callbacks must not replay, collide, or cross-bind between users or workflows
- Expected Immunefi impact: retrieve protected data or secrets through cross-workflow confusion
- Fast validation: Send duplicate request IDs and conflicting workflow selectors; assert exactly one authorized workflow and one callback slot are used.
