# Q2938: Push outbound convert - outbound ordering stuck malformed row

## Question
If a user cause one public Push-chain transaction to fan out into multiple pending outbounds to the same or different chains, can `convertOutboundToEvent` be pushed into a path where the order and grouping of multiple pending outbounds returned by `GetAllPendingOutbounds` causes it to accept malformed outbound data into the local queue where it blocks execution, retries forever, or starves later outbounds, so that `TxID`, `UniversalTxId`, and origin-chain references stay bound together across signing, broadcast, and resolution no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/push/event_parser.go:convertOutboundToEvent
- Entrypoint: cause one public Push-chain transaction to fan out into multiple pending outbounds to the same or different chains
- Attacker controls: the order and grouping of multiple pending outbounds returned by `GetAllPendingOutbounds`
- Exploit idea: accept malformed outbound data into the local queue where it blocks execution, retries forever, or starves later outbounds
- Invariant to test: `TxID`, `UniversalTxId`, and origin-chain references stay bound together across signing, broadcast, and resolution
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: create pending outbounds on a local Push chain, compare raw gRPC responses with stored `store.Event` JSON, and verify no field drift occurs
