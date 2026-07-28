# Q2373: Push outbound store - pc origin lost correlation

## Question
If a user cause one public Push-chain transaction to fan out into multiple pending outbounds to the same or different chains, can `storeEvent` be pushed into a path where `PcTxHash`, `LogIndex`, and revert recipient or revert message fields attached to the outbound causes it to lose the binding between `TxID`, `UniversalTxId`, and originating Push-chain fields so a later signature or refund applies to the wrong outbound, so that each pending outbound must project into one local event with the same IDs, amounts, and destination semantics as on Push Chain no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/push/event_listener.go:storeEvent
- Entrypoint: cause one public Push-chain transaction to fan out into multiple pending outbounds to the same or different chains
- Attacker controls: `PcTxHash`, `LogIndex`, and revert recipient or revert message fields attached to the outbound
- Exploit idea: lose the binding between `TxID`, `UniversalTxId`, and originating Push-chain fields so a later signature or refund applies to the wrong outbound
- Invariant to test: each pending outbound must project into one local event with the same IDs, amounts, and destination semantics as on Push Chain
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: feed malformed but user-reachable outbound parameters and watch whether later unrelated outbounds stop signing or resolving
