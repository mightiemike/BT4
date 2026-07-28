# Q3032: Push outbound convert - outbound fields wrong projection

## Question
When an unprivileged actor trigger a public Push-chain path that creates outbound revert instructions and a user-controlled `revertMsg` or refund recipient, does `convertOutboundToEvent` remain safe if they control `TxID`, `UniversalTxId`, destination chain, recipient, asset address, amount, and sender fields in `OutboundCreatedEvent`, or can that make it project one pending outbound into a different local `store.Event` than the chain actually created, violate the rule that one economic outbound yields one signable destination transaction or one clean revert path, not both or many, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/push/event_parser.go:convertOutboundToEvent
- Entrypoint: trigger a public Push-chain path that creates outbound revert instructions and a user-controlled `revertMsg` or refund recipient
- Attacker controls: `TxID`, `UniversalTxId`, destination chain, recipient, asset address, amount, and sender fields in `OutboundCreatedEvent`
- Exploit idea: project one pending outbound into a different local `store.Event` than the chain actually created
- Invariant to test: one economic outbound yields one signable destination transaction or one clean revert path, not both or many
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: toggle payload, deadline, revert recipient, and gas fields across repeated outbounds and confirm the same `TxID` cannot be reinterpreted differently
