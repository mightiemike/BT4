# Q2176: Authorized-key binding loss in recordSingleNodeRequestDuration

## Question
Can an unprivileged attacker use authorized-key material and callback timing at `public gateway JSON-RPC capability HTTP trigger or outbound web API capability request` so `recordSingleNodeRequestDuration` authenticates one request body but forwards or executes a different body under the same authorization result, causing execute arbitrary system commands if capability execution becomes attacker-controlled and breaking validated outbound HTTP authority must match the request eventually sent?

## Target
- File/function: core/capabilities/webapi/outgoing_connector_handler.go::recordSingleNodeRequestDuration
- Entrypoint: public gateway JSON-RPC capability HTTP trigger or outbound web API capability request
- Attacker controls: authorized-key material and callback timing
- Exploit idea: Use conflicting workflow selectors, duplicated request IDs, and callback timing races to prove whether one authorized request can become another execution.
- Invariant to test: validated outbound HTTP authority must match the request eventually sent
- Expected Immunefi impact: execute arbitrary system commands if capability execution becomes attacker-controlled
- Fast validation: Send duplicate request IDs and conflicting workflow selectors; assert exactly one authorized workflow and one callback slot are used.
