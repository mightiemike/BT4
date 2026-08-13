# Q2363: Outbound authority mismatch in NewHTTPTriggerHandler

## Question
Can an unprivileged attacker shape outbound HTTP target, headers, body, and response correlation IDs at `public gateway JSON-RPC capability HTTP trigger or outbound web API capability request` so `NewHTTPTriggerHandler` validates a benign outbound web/API target but actually sends to a different authority or header/body context, causing authentication bypass or unauthorized workflow/capability execution and violating request IDs and callbacks must not replay, collide, or cross-bind between users or workflows?

## Target
- File/function: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go::NewHTTPTriggerHandler
- Entrypoint: public gateway JSON-RPC capability HTTP trigger or outbound web API capability request
- Attacker controls: outbound HTTP target, headers, body, and response correlation IDs
- Exploit idea: Use conflicting workflow selectors, duplicated request IDs, and callback timing races to prove whether one authorized request can become another execution.
- Invariant to test: request IDs and callbacks must not replay, collide, or cross-bind between users or workflows
- Expected Immunefi impact: authentication bypass or unauthorized workflow/capability execution
- Fast validation: Send duplicate request IDs and conflicting workflow selectors; assert exactly one authorized workflow and one callback slot are used.
