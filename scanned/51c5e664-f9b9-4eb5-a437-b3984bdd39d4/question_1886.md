# Q1886: EVM signature-data decode - abi offsets partial decode

## Question
If a user emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries, can `decodeSignatureData` be pushed into a path where dynamic ABI offsets for payload bytes and signature data inside log data causes it to accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation, so that every observed EVM log maps to exactly one canonical `EventData` payload and one confirmation policy no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/evm/event_parser.go:decodeSignatureData
- Entrypoint: emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries
- Attacker controls: dynamic ABI offsets for payload bytes and signature data inside log data
- Exploit idea: accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation
- Invariant to test: every observed EVM log maps to exactly one canonical `EventData` payload and one confirmation policy
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: force a shallow reorg or replay around the confirmer window and check whether conflicting `EventData` rows or duplicate vote attempts appear
