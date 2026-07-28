# Q1564: SVM send_funds ingest - program data length truncation

## Question
If a user emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index, can `parseSendFundsEvent` be pushed into a path where base64-encoded `Program data:` bytes, including lengths, discriminators, and trailing fields causes it to accept truncated Solana event data as a valid inbound or outbound observation with partial semantics, so that only well-formed SVM gateway bytes can become an inbound or outbound observation no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseSendFundsEvent
- Entrypoint: emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index
- Attacker controls: base64-encoded `Program data:` bytes, including lengths, discriminators, and trailing fields
- Exploit idea: accept truncated Solana event data as a valid inbound or outbound observation with partial semantics
- Invariant to test: only well-formed SVM gateway bytes can become an inbound or outbound observation
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: toggle base58, zero bytes, and alternate-length address encodings and inspect whether economic meaning changes after normalization
