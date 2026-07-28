# Q1527: Push outbound store - outbound fields wrong projection

## Question
When an unprivileged actor cause one public Push-chain transaction to fan out into multiple pending outbounds to the same or different chains, does `storeEvent` remain safe if they control `TxID`, `UniversalTxId`, destination chain, recipient, asset address, amount, and sender fields in `OutboundCreatedEvent`, or can that make it project one pending outbound into a different local `store.Event` than the chain actually created, violate the rule that `TxID`, `UniversalTxId`, and origin-chain references stay bound together across signing, broadcast, and resolution, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/push/event_listener.go:storeEvent
- Entrypoint: cause one public Push-chain transaction to fan out into multiple pending outbounds to the same or different chains
- Attacker controls: `TxID`, `UniversalTxId`, destination chain, recipient, asset address, amount, and sender fields in `OutboundCreatedEvent`
- Exploit idea: project one pending outbound into a different local `store.Event` than the chain actually created
- Invariant to test: `TxID`, `UniversalTxId`, and origin-chain references stay bound together across signing, broadcast, and resolution
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: create pending outbounds on a local Push chain, compare raw gRPC responses with stored `store.Event` JSON, and verify no field drift occurs
