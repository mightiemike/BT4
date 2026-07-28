# Q1622: Push outbound convert - outbound fields lost correlation

## Question
When an unprivileged actor cause one public Push-chain transaction to fan out into multiple pending outbounds to the same or different chains, does `convertOutboundToEvent` remain safe if they control `TxID`, `UniversalTxId`, destination chain, recipient, asset address, amount, and sender fields in `OutboundCreatedEvent`, or can that make it lose the binding between `TxID`, `UniversalTxId`, and originating Push-chain fields so a later signature or refund applies to the wrong outbound, violate the rule that one economic outbound yields one signable destination transaction or one clean revert path, not both or many, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/push/event_parser.go:convertOutboundToEvent
- Entrypoint: cause one public Push-chain transaction to fan out into multiple pending outbounds to the same or different chains
- Attacker controls: `TxID`, `UniversalTxId`, destination chain, recipient, asset address, amount, and sender fields in `OutboundCreatedEvent`
- Exploit idea: lose the binding between `TxID`, `UniversalTxId`, and originating Push-chain fields so a later signature or refund applies to the wrong outbound
- Invariant to test: one economic outbound yields one signable destination transaction or one clean revert path, not both or many
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: submit one transaction that produces multiple outbounds and check whether local rows stay correctly paired by index and ID under retries
