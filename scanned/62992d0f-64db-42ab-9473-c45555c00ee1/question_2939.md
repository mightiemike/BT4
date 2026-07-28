# Q2939: Push outbound vote msg - outbound ordering stuck malformed row

## Question
When an unprivileged actor cause one public Push-chain transaction to fan out into multiple pending outbounds to the same or different chains, does `voteOutbound` remain safe if they control the order and grouping of multiple pending outbounds returned by `GetAllPendingOutbounds`, or can that make it accept malformed outbound data into the local queue where it blocks execution, retries forever, or starves later outbounds, violate the rule that `TxID`, `UniversalTxId`, and origin-chain references stay bound together across signing, broadcast, and resolution, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/pushsigner/vote.go:voteOutbound
- Entrypoint: cause one public Push-chain transaction to fan out into multiple pending outbounds to the same or different chains
- Attacker controls: the order and grouping of multiple pending outbounds returned by `GetAllPendingOutbounds`
- Exploit idea: accept malformed outbound data into the local queue where it blocks execution, retries forever, or starves later outbounds
- Invariant to test: `TxID`, `UniversalTxId`, and origin-chain references stay bound together across signing, broadcast, and resolution
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: create pending outbounds on a local Push chain, compare raw gRPC responses with stored `store.Event` JSON, and verify no field drift occurs
