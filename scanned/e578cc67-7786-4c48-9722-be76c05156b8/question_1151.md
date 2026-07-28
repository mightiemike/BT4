# Q1151: Push outbound store - outbound ordering wrong projection

## Question
When an unprivileged actor submit a public Push-chain flow that creates a pending outbound with attacker-chosen destination, recipient, amount, payload, and gas parameters, does `storeEvent` remain safe if they control the order and grouping of multiple pending outbounds returned by `GetAllPendingOutbounds`, or can that make it project one pending outbound into a different local `store.Event` than the chain actually created, violate the rule that `TxID`, `UniversalTxId`, and origin-chain references stay bound together across signing, broadcast, and resolution, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/push/event_listener.go:storeEvent
- Entrypoint: submit a public Push-chain flow that creates a pending outbound with attacker-chosen destination, recipient, amount, payload, and gas parameters
- Attacker controls: the order and grouping of multiple pending outbounds returned by `GetAllPendingOutbounds`
- Exploit idea: project one pending outbound into a different local `store.Event` than the chain actually created
- Invariant to test: `TxID`, `UniversalTxId`, and origin-chain references stay bound together across signing, broadcast, and resolution
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: toggle payload, deadline, revert recipient, and gas fields across repeated outbounds and confirm the same `TxID` cannot be reinterpreted differently
