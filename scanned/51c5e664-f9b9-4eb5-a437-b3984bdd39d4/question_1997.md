# Q1997: Push outbound store - gas/deadline lost correlation

## Question
When an unprivileged actor cause one public Push-chain transaction to fan out into multiple pending outbounds to the same or different chains, does `storeEvent` remain safe if they control gas price, gas limit, gas fee, and signing deadline carried into the pending outbound entry, or can that make it lose the binding between `TxID`, `UniversalTxId`, and originating Push-chain fields so a later signature or refund applies to the wrong outbound, violate the rule that `TxID`, `UniversalTxId`, and origin-chain references stay bound together across signing, broadcast, and resolution, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/push/event_listener.go:storeEvent
- Entrypoint: cause one public Push-chain transaction to fan out into multiple pending outbounds to the same or different chains
- Attacker controls: gas price, gas limit, gas fee, and signing deadline carried into the pending outbound entry
- Exploit idea: lose the binding between `TxID`, `UniversalTxId`, and originating Push-chain fields so a later signature or refund applies to the wrong outbound
- Invariant to test: `TxID`, `UniversalTxId`, and origin-chain references stay bound together across signing, broadcast, and resolution
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: create pending outbounds on a local Push chain, compare raw gRPC responses with stored `store.Event` JSON, and verify no field drift occurs
