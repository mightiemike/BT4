# Q2467: Push outbound store - pc origin duplicate sign target

## Question
When an unprivileged actor cause one public Push-chain transaction to fan out into multiple pending outbounds to the same or different chains, does `storeEvent` remain safe if they control `PcTxHash`, `LogIndex`, and revert recipient or revert message fields attached to the outbound, or can that make it materialize multiple local sign targets from one economic outbound, enabling duplicate broadcasts or inconsistent refunds, violate the rule that `TxID`, `UniversalTxId`, and origin-chain references stay bound together across signing, broadcast, and resolution, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/push/event_listener.go:storeEvent
- Entrypoint: cause one public Push-chain transaction to fan out into multiple pending outbounds to the same or different chains
- Attacker controls: `PcTxHash`, `LogIndex`, and revert recipient or revert message fields attached to the outbound
- Exploit idea: materialize multiple local sign targets from one economic outbound, enabling duplicate broadcasts or inconsistent refunds
- Invariant to test: `TxID`, `UniversalTxId`, and origin-chain references stay bound together across signing, broadcast, and resolution
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: create pending outbounds on a local Push chain, compare raw gRPC responses with stored `store.Event` JSON, and verify no field drift occurs
