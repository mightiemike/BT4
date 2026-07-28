# Q0680: Push outbound poll - gas/deadline stuck malformed row

## Question
When an unprivileged actor submit a public Push-chain flow that creates a pending outbound with attacker-chosen destination, recipient, amount, payload, and gas parameters, does `pollOutboundEvents` remain safe if they control gas price, gas limit, gas fee, and signing deadline carried into the pending outbound entry, or can that make it accept malformed outbound data into the local queue where it blocks execution, retries forever, or starves later outbounds, violate the rule that one economic outbound yields one signable destination transaction or one clean revert path, not both or many, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/push/event_listener.go:pollOutboundEvents
- Entrypoint: submit a public Push-chain flow that creates a pending outbound with attacker-chosen destination, recipient, amount, payload, and gas parameters
- Attacker controls: gas price, gas limit, gas fee, and signing deadline carried into the pending outbound entry
- Exploit idea: accept malformed outbound data into the local queue where it blocks execution, retries forever, or starves later outbounds
- Invariant to test: one economic outbound yields one signable destination transaction or one clean revert path, not both or many
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: feed malformed but user-reachable outbound parameters and watch whether later unrelated outbounds stop signing or resolving
