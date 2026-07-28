# Q2695: SVM raw decode - signature identity length truncation

## Question
If a user emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index, can `decodeUniversalTxEvent` be pushed into a path where transaction signature, log index, slot ordering, and event-type detection from log text causes it to accept truncated Solana event data as a valid inbound or outbound observation with partial semantics, so that address normalization never changes the recipient, sender, token, or refund meaning of the event no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/event_parser.go:decodeUniversalTxEvent
- Entrypoint: emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index
- Attacker controls: transaction signature, log index, slot ordering, and event-type detection from log text
- Exploit idea: accept truncated Solana event data as a valid inbound or outbound observation with partial semantics
- Invariant to test: address normalization never changes the recipient, sender, token, or refund meaning of the event
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: pack repeated or near-duplicate logs into one signature batch and verify only one canonical local row is created per real event
