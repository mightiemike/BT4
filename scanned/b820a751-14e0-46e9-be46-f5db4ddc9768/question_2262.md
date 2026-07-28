# Q2262: EVM signature-data decode - value fields partial decode

## Question
If a user emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries, can `decodeSignatureData` be pushed into a path where token, amount, revert recipient, tx type, and `fromCEA` bits encoded in the event payload causes it to accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation, so that reorged, malformed, or wrong-method EVM logs never reach `StatusCompleted` no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/evm/event_parser.go:decodeSignatureData
- Entrypoint: emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries
- Attacker controls: token, amount, revert recipient, tx type, and `fromCEA` bits encoded in the event payload
- Exploit idea: accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation
- Invariant to test: reorged, malformed, or wrong-method EVM logs never reach `StatusCompleted`
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: emit crafted gateway logs on a fork or local EVM devnet and compare raw log bytes against the persisted `store.Event` row and the resulting vote message
