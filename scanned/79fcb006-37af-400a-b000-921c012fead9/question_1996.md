# Q1996: Push outbound poll - gas/deadline lost correlation

## Question
If a user cause one public Push-chain transaction to fan out into multiple pending outbounds to the same or different chains, can `pollOutboundEvents` be pushed into a path where gas price, gas limit, gas fee, and signing deadline carried into the pending outbound entry causes it to lose the binding between `TxID`, `UniversalTxId`, and originating Push-chain fields so a later signature or refund applies to the wrong outbound, so that `TxID`, `UniversalTxId`, and origin-chain references stay bound together across signing, broadcast, and resolution no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/push/event_listener.go:pollOutboundEvents
- Entrypoint: cause one public Push-chain transaction to fan out into multiple pending outbounds to the same or different chains
- Attacker controls: gas price, gas limit, gas fee, and signing deadline carried into the pending outbound entry
- Exploit idea: lose the binding between `TxID`, `UniversalTxId`, and originating Push-chain fields so a later signature or refund applies to the wrong outbound
- Invariant to test: `TxID`, `UniversalTxId`, and origin-chain references stay bound together across signing, broadcast, and resolution
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: create pending outbounds on a local Push chain, compare raw gRPC responses with stored `store.Event` JSON, and verify no field drift occurs
