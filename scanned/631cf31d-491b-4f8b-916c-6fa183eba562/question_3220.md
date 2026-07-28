# Q3220: Push outbound convert - outbound fields duplicate sign target

## Question
Can an unprivileged attacker trigger a public Push-chain path that creates outbound revert instructions and a user-controlled `revertMsg` or refund recipient and use control over `TxID`, `UniversalTxId`, destination chain, recipient, asset address, amount, and sender fields in `OutboundCreatedEvent` so that `convertOutboundToEvent` materialize multiple local sign targets from one economic outbound, enabling duplicate broadcasts or inconsistent refunds, breaking the invariant that each pending outbound must project into one local event with the same IDs, amounts, and destination semantics as on Push Chain and leading to direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/push/event_parser.go:convertOutboundToEvent
- Entrypoint: trigger a public Push-chain path that creates outbound revert instructions and a user-controlled `revertMsg` or refund recipient
- Attacker controls: `TxID`, `UniversalTxId`, destination chain, recipient, asset address, amount, and sender fields in `OutboundCreatedEvent`
- Exploit idea: materialize multiple local sign targets from one economic outbound, enabling duplicate broadcasts or inconsistent refunds
- Invariant to test: each pending outbound must project into one local event with the same IDs, amounts, and destination semantics as on Push Chain
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: create pending outbounds on a local Push chain, compare raw gRPC responses with stored `store.Event` JSON, and verify no field drift occurs
