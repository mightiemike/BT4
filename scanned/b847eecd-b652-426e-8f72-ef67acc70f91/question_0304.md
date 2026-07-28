# Q0304: Push outbound poll - outbound fields stuck malformed row

## Question
Can an unprivileged attacker submit a public Push-chain flow that creates a pending outbound with attacker-chosen destination, recipient, amount, payload, and gas parameters and use control over `TxID`, `UniversalTxId`, destination chain, recipient, asset address, amount, and sender fields in `OutboundCreatedEvent` so that `pollOutboundEvents` accept malformed outbound data into the local queue where it blocks execution, retries forever, or starves later outbounds, breaking the invariant that malformed outbound data cannot poison the queue for unrelated user outbounds and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/push/event_listener.go:pollOutboundEvents
- Entrypoint: submit a public Push-chain flow that creates a pending outbound with attacker-chosen destination, recipient, amount, payload, and gas parameters
- Attacker controls: `TxID`, `UniversalTxId`, destination chain, recipient, asset address, amount, and sender fields in `OutboundCreatedEvent`
- Exploit idea: accept malformed outbound data into the local queue where it blocks execution, retries forever, or starves later outbounds
- Invariant to test: malformed outbound data cannot poison the queue for unrelated user outbounds
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: create pending outbounds on a local Push chain, compare raw gRPC responses with stored `store.Event` JSON, and verify no field drift occurs
