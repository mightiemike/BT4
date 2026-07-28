# Q3783: Push outbound store - pc origin wrong projection

## Question
If a user trigger a public Push-chain path that creates outbound revert instructions and a user-controlled `revertMsg` or refund recipient, can `storeEvent` be pushed into a path where `PcTxHash`, `LogIndex`, and revert recipient or revert message fields attached to the outbound causes it to project one pending outbound into a different local `store.Event` than the chain actually created, so that each pending outbound must project into one local event with the same IDs, amounts, and destination semantics as on Push Chain no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/push/event_listener.go:storeEvent
- Entrypoint: trigger a public Push-chain path that creates outbound revert instructions and a user-controlled `revertMsg` or refund recipient
- Attacker controls: `PcTxHash`, `LogIndex`, and revert recipient or revert message fields attached to the outbound
- Exploit idea: project one pending outbound into a different local `store.Event` than the chain actually created
- Invariant to test: each pending outbound must project into one local event with the same IDs, amounts, and destination semantics as on Push Chain
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: create pending outbounds on a local Push chain, compare raw gRPC responses with stored `store.Event` JSON, and verify no field drift occurs
