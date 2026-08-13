# Q2317: Boundary preservation edge case in send #1

## Question
Can an unprivileged attacker use JSON-RPC requestID, method, workflow selector, and params JSON at `public gateway JSON-RPC capability HTTP trigger or outbound web API capability request` so `send` reaches a concrete path to retrieve protected data or secrets through cross-workflow confusion by breaking the invariant that request IDs and callbacks must not replay, collide, or cross-bind between users or workflows, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/services/gateway/handlers/capabilities/v2/http_handler.go::send
- Entrypoint: public gateway JSON-RPC capability HTTP trigger or outbound web API capability request
- Attacker controls: JSON-RPC requestID, method, workflow selector, and params JSON
- Exploit idea: Use conflicting workflow selectors, duplicated request IDs, and callback timing races to prove whether one authorized request can become another execution.
- Invariant to test: request IDs and callbacks must not replay, collide, or cross-bind between users or workflows
- Expected Immunefi impact: retrieve protected data or secrets through cross-workflow confusion
- Fast validation: Send duplicate request IDs and conflicting workflow selectors; assert exactly one authorized workflow and one callback slot are used.
