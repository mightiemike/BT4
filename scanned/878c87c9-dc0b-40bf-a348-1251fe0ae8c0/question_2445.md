# Q2445: Workflow selector confusion in resolveWorkflowID

## Question
Can an unprivileged attacker send JSON-RPC requestID, method, workflow selector, and params JSON at `public gateway JSON-RPC capability HTTP trigger or outbound web API capability request` so `resolveWorkflowID` authorizes one workflow identity but routes execution to another, causing authentication bypass or unauthorized workflow/capability execution and violating workflow resolution must map one request to exactly one authorized workflow owner?

## Target
- File/function: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go::resolveWorkflowID
- Entrypoint: public gateway JSON-RPC capability HTTP trigger or outbound web API capability request
- Attacker controls: JSON-RPC requestID, method, workflow selector, and params JSON
- Exploit idea: Use conflicting workflow selectors, duplicated request IDs, and callback timing races to prove whether one authorized request can become another execution.
- Invariant to test: workflow resolution must map one request to exactly one authorized workflow owner
- Expected Immunefi impact: authentication bypass or unauthorized workflow/capability execution
- Fast validation: Send duplicate request IDs and conflicting workflow selectors; assert exactly one authorized workflow and one callback slot are used.
